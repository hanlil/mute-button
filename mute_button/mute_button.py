import asyncio
import logging as log
import os
import pyaudiowpatch as pyaudio
import queue
import reflex as rx
import shutil
import uuid
import wave

from collections import defaultdict

# Patch must be imported before reflex_dynoselect to apply
import mute_button.dynoselect_patch
from reflex_dynoselect import dynoselect

from mute_button.component_builders import *


log.basicConfig(level=log.INFO)

UPLOAD_DIR = rx.get_upload_dir()
AUDIO_DIR = os.path.join(UPLOAD_DIR, 'audio')
SPEAKERS_DIR = os.path.join(AUDIO_DIR, 'speakers')
RECORDING_DIR = os.path.join(AUDIO_DIR, 'tmp')
os.makedirs(SPEAKERS_DIR, exist_ok=True)
if os.path.exists(RECORDING_DIR):
    shutil.rmtree(RECORDING_DIR)
os.makedirs(RECORDING_DIR)

client_audio_recording_queues = defaultdict(queue.Queue)

def _get_speaker_options():
    return [{'value': i, 'label': i} for i in os.listdir(SPEAKERS_DIR) if os.path.isdir(os.path.join(SPEAKERS_DIR, i))]

speaker_options = _get_speaker_options()


class State(rx.State):
    _device_map: dict
    _loopback_devices: dict
    loopback_device: str
    _playback_devices: dict
    playback_device: str
    do_forward: bool = False
    do_mute: bool = False
    do_record: bool = False
    recording_exists: bool = False
    recording_path_cache: list = []
    speaker_for_sample: str
    save_sample_disabled: bool = True

    @rx.var
    def loopback_device_names(self) -> list:
        return list(self._loopback_devices.keys())

    @rx.event
    def set_loopback_device(self, value: str):
        self.loopback_device = value

    @rx.var
    def playback_device_names(self) -> list:
        return list(self._playback_devices.keys())

    @rx.event
    def set_playback_device(self, value: str):
        self.playback_device = value

    def _get_device_warning(self) -> str:
        if len(self._loopback_devices) < 1 or len(self._playback_devices) < 1 or len(set(list(self._loopback_devices.keys()) + list(self._playback_devices.keys()))) < 2:
            return 'Not enough audio devices available (capture device and playback device cannot be the same).'
        elif self.loopback_device == '' and self.playback_device  == '':
            return 'Select a capture device and a playback device.'
        elif self.loopback_device  == '':
            return 'Select a capture device.'
        elif self.playback_device  == '':
            return 'Select a playback device.'
        elif self.loopback_device == self.playback_device:
            return 'Capture device and playback device cannot be the same.'
        return ''

    @rx.var
    def device_warning(self) -> str:
        return self._get_device_warning()

    @rx.var
    def devices_invalid(self) -> bool:
        return self._get_device_warning() != ''

    @rx.event
    def toggle_forward(self, value: bool):
        self.do_forward = value

    @rx.event
    def start_recording(self):
        client_audio_recording_queues[self.router.session.client_token] = queue.Queue()
        self.do_record = True
        self.recording_exists = False
        self.recording_path_cache = []
        self.save_sample_disabled = True

    @rx.event
    def stop_recording(self):
        loopback_device_info = self._device_map.get(self._loopback_devices.get(self.loopback_device))
        if loopback_device_info is not None:
            q = client_audio_recording_queues[self.router.session.client_token]
            recorded_frames = []
            while not q.empty():
                try:
                    recorded_frames.append(q.get_nowait())
                except queue.Empty:
                    break
            recording_path = os.path.join(RECORDING_DIR, str(uuid.uuid4()) + '.wav')
            with wave.open(recording_path, 'wb') as wf:
                wf.setnchannels(loopback_device_info['maxInputChannels']) # Input instead of output due to loopback
                wf.setsampwidth(pyaudio.PyAudio().get_sample_size(pyaudio.paInt16))
                wf.setframerate(int(loopback_device_info['defaultSampleRate']))
                wf.writeframes(b''.join(recorded_frames))
            self.recording_exists = True
            self.recording_path_cache = [os.path.relpath(recording_path, UPLOAD_DIR)]
            self.save_sample_disabled = self.speaker_for_sample is None or self.speaker_for_sample == ''
        client_audio_recording_queues[self.router.session.client_token] = queue.Queue()
        self.do_record = False

    @rx.event
    def select_speaker_for_sample(self, selected):
        self.speaker_for_sample = selected.get('label', '')
        self.save_sample_disabled = not self.recording_exists

    @rx.event
    def save_sample(self):
        if len(self.speaker_for_sample) > 0 and len(self.recording_path_cache) > 0:
            speaker_dir = os.path.join(SPEAKERS_DIR, self.speaker_for_sample)
            os.makedirs(speaker_dir, exist_ok=True)
            src = os.path.join(UPLOAD_DIR, self.recording_path_cache[0])
            dst = os.path.join(speaker_dir, os.path.relpath(src, RECORDING_DIR))
            shutil.copy(src, dst)
            global speaker_options
            speaker_options = _get_speaker_options()

    @rx.event
    def find_audio_devices(self):
        with pyaudio.PyAudio() as pa:
            wasapi_devices = pa.get_device_info_generator_by_host_api(host_api_type=pyaudio.paWASAPI)
            self._device_map = {dev['index']: dev for dev in wasapi_devices}
            self._loopback_devices = {dev['name'].removesuffix(' [Loopback]'): dev['index'] for dev in self._device_map.values() if dev['isLoopbackDevice']}
            self._playback_devices = {dev['name']: dev['index'] for dev in self._device_map.values() if dev['maxOutputChannels'] > 0}
            log.info('Found {} loopback device(s) and {} playback device(s)'.format(len(self._loopback_devices.keys()), len(self._playback_devices.keys())))

    def _create_audio_callback(self, output_stream, client_token):
        def callback(in_data, frame_count, time_info, status):
            if self.do_forward and not self.do_mute:
                output_stream.write(in_data)
            if self.do_record:
                client_audio_recording_queues[client_token].put(in_data)
            return in_data, pyaudio.paContinue
        return callback

    @rx.event(background=True)
    async def process_audio(self):
        loopback_device = None
        playback_device = None
        audio_input_stream = None
        audio_output_stream = None
        processing = False
        stale = False
        pa = pyaudio.PyAudio()

        while not stale:
            if not processing:
                async with self:
                    if self.loopback_device != '' and self.playback_device != '' and self.loopback_device != self.playback_device:
                        loopback_device = self.loopback_device
                        playback_device = self.playback_device
                        loopback_device_info = self._device_map.get(self._loopback_devices.get(self.loopback_device))
                        playback_device_info = self._device_map.get(self._playback_devices.get(self.playback_device))
                        if loopback_device_info and playback_device_info:
                            input_sample_rate = int(loopback_device_info['defaultSampleRate'])
                            frames_per_buffer = int(input_sample_rate / 20)
                            audio_output_stream = pa.open(format=pyaudio.paInt16, channels=playback_device_info['maxOutputChannels'],
                                                          rate=int(playback_device_info['defaultSampleRate']), frames_per_buffer=frames_per_buffer, output=True,
                                                          output_device_index=playback_device_info['index'])
                            client_audio_recording_queues[self.router.session.client_token] = queue.Queue()
                            input_callback = self._create_audio_callback(audio_output_stream, self.router.session.client_token)
                            audio_input_stream = pa.open(format=pyaudio.paInt16, channels=loopback_device_info['maxInputChannels'],
                                                         rate=input_sample_rate, frames_per_buffer=frames_per_buffer, input=True,
                                                         input_device_index=loopback_device_info['index'], stream_callback=input_callback)
                            processing = True
                            log.info('Processing audio from {} to {}.'.format(loopback_device, playback_device))
                if not processing:
                    async with self:
                        if self.router.session.client_token not in app.event_namespace.token_to_sid:
                            stale = True
                    await asyncio.sleep(1)
            while processing:
                async with self:
                    if self.router.session.client_token not in app.event_namespace.token_to_sid:
                        stale = True
                    if stale or self.loopback_device != loopback_device or self.playback_device != playback_device:
                        audio_input_stream.stop_stream()
                        audio_input_stream.close()
                        audio_output_stream.stop_stream()
                        audio_output_stream.close()
                        processing = False
                        del client_audio_recording_queues[self.router.session.client_token]
                        if not stale:
                            self.do_record = False
                        log.info('Stopped processing audio from {} to {}.'.format(loopback_device, playback_device))
                if processing:
                    await asyncio.sleep(1)
        log.info('Stale audio processing handler removed.')


@rx.page(on_load=[State.find_audio_devices, State.process_audio])
def index() -> rx.Component:
    return rx.container(
        rx.color_mode.button(position='top-right'),
        rx.vstack(
            rx.heading('Mute-button App', size='7', align='center'),
            rx.hstack(
                rx.vstack(
                    titled_card(
                        rx.vstack(
                            labeled_component(
                                rx.select(items=State.loopback_device_names, value=State.loopback_device, on_change=State.set_loopback_device, width='350px'),
                                'Capture device'
                            ),
                            labeled_component(
                                rx.select(State.playback_device_names, value=State.playback_device, on_change=State.set_playback_device, width='350px'),
                                'Playback device'
                            ),
                            spacing='4',
                        ),
                        title='Audio devices',
                        title_spacing='4'
                    ),
                    rx.match(
                        State.device_warning,
                        ('', rx.fragment()),
                        rx.callout(
                            State.device_warning,
                            icon='triangle_alert', variant='surface', color_scheme='red', role='alert', width='100%'
                        ),
                    ),
                    width='100%',
                    flex='1',
                ),
                rx.vstack(
                    titled_card(
                        rx.vstack(
                            labeled_component(
                                rx.switch(checked=State.do_forward, on_change=State.toggle_forward, disabled=State.devices_invalid),
                                'Forward audio from capture to playback device'
                            ),
                            labeled_component(
                                rx.vstack(
                                    rx.hstack(
                                        rx.cond(
                                            State.do_record,
                                            rx.button('Stop recording', variant='surface', color_scheme='red', width='177px', on_click=State.stop_recording, disabled=State.devices_invalid),
                                            rx.button('Start recording', variant='surface', color_sheme='indigo', width='177px', on_click=State.start_recording, disabled=State.devices_invalid),
                                        ),
                                        rx.foreach(
                                            State.recording_path_cache,
                                            lambda recording_path: rx.audio(
                                                url=rx.get_upload_url(recording_path),
                                                width='300px',
                                                height='32px',
                                            )
                                        )
                                    ),
                                    rx.hstack(
                                        rx.button('Save sample to speaker', variant='surface', color_sheme='indigo', width='177px', on_click=State.save_sample, disabled=State.save_sample_disabled),
                                        dynoselect(
                                            speaker_options,
                                            create_option=dict(value='custom', label='Create new "{}"'),
                                            placeholder='Select or add speaker',
                                            search_placeholder='Search',
                                            on_select=State.select_speaker_for_sample,
                                        )
                                    ),
                                ),
                                'Audio sample recording'
                            ),
                            spacing='4',
                        ),
                        title='Audio controls',
                        title_spacing='4'
                    ),
                    width='100%',
                    flex='2',
                ),
                spacing='5',
                width='100%',
            ),
            spacing='5',
        ),
        size='4'
    )


app = rx.App()

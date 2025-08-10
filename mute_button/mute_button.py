import asyncio
import logging as log
import pyaudiowpatch as pyaudio
import reflex as rx

from mute_button.component_builders import *


log.basicConfig(level=log.INFO)


class State(rx.State):
    _device_map: dict
    _loopback_devices: dict
    loopback_device: str
    _playback_devices: dict
    playback_device: str
    do_forward: bool = False
    do_mute: bool = False

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
        if self.loopback_device == '' and self.playback_device  == '':
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
    def find_audio_devices(self):
        with pyaudio.PyAudio() as pa:
            wasapi_devices = pa.get_device_info_generator_by_host_api(host_api_type=pyaudio.paWASAPI)
            self._device_map = {dev['index']: dev for dev in wasapi_devices}
            self._loopback_devices = {dev['name'].removesuffix(' [Loopback]'): dev['index'] for dev in self._device_map.values() if dev['isLoopbackDevice']}
            self._playback_devices = {dev['name']: dev['index'] for dev in self._device_map.values() if dev['maxOutputChannels'] > 0}
            log.info('Found {} loopback device(s) and {} playback device(s)'.format(len(self._loopback_devices.keys()), len(self._playback_devices.keys())))

    def _create_audio_callback(self, output_stream):
        def callback(in_data, frame_count, time_info, status):
            if self.do_forward and not self.do_mute:
                output_stream.write(in_data)
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
                            input_callback = self._create_audio_callback(audio_output_stream)
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
                            icon='triangle_alert', color_scheme='red', role='alert',
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

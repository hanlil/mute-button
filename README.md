# mute-button

Work in progress...

## Managing Dependencies with uv

This project uses [uv](https://docs.astral.sh/uv/) for Python package management.

### Adding a new dependency

```bash
uv add <package-name>
```

### Updating dependencies

To update all dependencies to their latest versions:

```bash
uv lock --upgrade
```

### Installing dependencies

After pulling changes or cloning the repository, install dependencies:

```bash
uv sync
```

## Running the Reflex Webapp

This is a [Reflex](https://reflex.dev/) web application.

### Development mode (with hot reload)

Start the development server with interactive hot-reload:

```bash
reflex run
```

The app will be available at `http://localhost:3000`. Changes to your code will automatically reload the app.

### Production build

Build the app for production:

```bash
reflex export
cd .web
npm run build
```

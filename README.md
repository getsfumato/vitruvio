<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/logo/dark.svg">
  <img alt="vitruvio" src="docs/logo/light.svg" height="48">
</picture>

Vitruvio is the runtime for a [Boltzmann brain](https://github.com/gaussia-labs/papers): portable, verifiable,
model-agnostic knowledge that any model can read and none of them owns. The brain keeps the evidence and everything
derived from it, and returns evidence rather than prose.

## Install

```console
curl -fsSL https://vitruvio.sh/install.sh | sh
vitruvio --version
```

## Skills for your agent

```console
npx skills add getsfumato/vitruvio
```

## Run it as a server

```console
docker run --rm -p 8080:8080 -e VITRUVIO_SERVER_ALLOWED_REGISTRIES=ghcr.io ghcr.io/getsfumato/vitruvio-server
```

The query API, and with [`apps/server/compose.yaml`](apps/server/compose.yaml) the ingest API and its worker too:
[vitruvio.sh/docs/server](https://vitruvio.sh/docs/server).

## Documentation

[vitruvio.sh/docs](https://vitruvio.sh/docs)

## License

MIT.

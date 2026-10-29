# Third-party notices

Terminal Pilot's original code is MIT licensed. Dependencies retain their own
copyright and license terms; the project license does not replace those terms.

| Component | Source | License / bundled notice |
| --- | --- | --- |
| ws 8.21.3 | https://github.com/websockets/ws | MIT, [vendor/ws/LICENSE](vendor/ws/LICENSE) |
| CPython 3.13.16 | https://github.com/astral-sh/python-build-standalone/releases/tag/20261013 | Python-2.0 / CNRI-Python and included component licenses |
| Node.js 22.23.2 | https://nodejs.org/download/release/v22.23.2/ | MIT and bundled third-party notices in Node's LICENSE |
| cryptography 50.0.2 | https://pypi.org/project/cryptography/50.0.2/ | Apache-2.0 OR BSD-3-Clause; wheel includes its dependency notices |
| cffi 2.0.0 | https://pypi.org/project/cffi/2.0.0/ | MIT; wheel includes license notices |
| pycparser 3.0 | https://pypi.org/project/pycparser/3.0/ | BSD-3-Clause; wheel includes license notices |
| truststore 0.10.4 (Windows) | https://pypi.org/project/truststore/0.10.4/ | MIT; installed package includes license notices |

Portable bundles include `Contents/Resources/ThirdPartyLicenses/` with CPython
build metadata, dependency notices, Node's complete LICENSE and the exact runtime
manifest. Python package licenses remain in their `*.dist-info/` directories
under `Contents/Resources/runtime/python/lib/python3.13/site-packages/`.
Unused Tcl/Tk GUI runtime and Python package installer are excluded.

The runtime lock file pins official artifacts and SHA-256 digests. Codex, Claude
Code and Tailscale are not redistributed; users install and authenticate them
independently under their respective terms.

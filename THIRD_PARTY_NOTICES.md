# Third-party notices

The archived model files are copied byte-for-byte from `commaai/openpilot` and remain the
copyright of Comma.ai, Inc. They are distributed under openpilot's MIT license:

> Copyright (c) 2018, Comma.ai, Inc.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software
> and associated documentation files (the "Software"), to deal in the Software without
> restriction, including without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
> Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
> BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Source: https://github.com/commaai/openpilot/blob/master/LICENSE

## Vendored compiler tooling

`ci/qcom/vendor.py`, `ci/qcom/compile.py` and `ci/qcom/serialization.py` are derived from
`commaai/openpilot` at commit `555f48c5d28709f039b79f3f6105e51305edd4b5` (MIT, copyright
Comma.ai, Inc.), and the a630 toolchain they fetch is pinned by URL and SHA-256 in
`ci/qcom/toolchain.py`. Compiled build artifacts published from this repository are derived
works of the archived MIT-licensed models above.

Source: https://github.com/commaai/openpilot/tree/555f48c5d28709f039b79f3f6105e51305edd4b5


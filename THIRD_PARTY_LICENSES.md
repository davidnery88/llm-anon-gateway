# Third-Party Licenses

This project includes third-party components under their own licenses.
This file provides attribution and license information as required by
those licenses.

---

## Python Dependencies

### gateway/requirements.txt

#### fastapi (MIT)
- Version: 0.115.5
- Copyright: © 2018 Sebastián Ramírez
- License: MIT License
- Repository: https://github.com/tiangolo/fastapi

```
MIT License

Copyright (c) 2018 Sebastián Ramírez

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

#### uvicorn (BSD-3-Clause)
- Version: 0.32.1
- Copyright: © Encode OSS Ltd.
- License: BSD 3-Clause License
- Repository: https://github.com/encode/uvicorn

#### httpx (BSD-3-Clause)
- Version: 0.27.2
- Copyright: © Encode OSS Ltd.
- License: BSD 3-Clause License
- Repository: https://github.com/encode/httpx

#### asyncpg (Apache-2.0)
- Version: 0.30.0
- Copyright: © MagicStack Inc.
- License: Apache License 2.0
- Repository: https://github.com/MagicStack/asyncpg

#### python-dotenv (BSD-3-Clause)
- Version: 1.0.1
- Copyright: © Saurabh Kumar
- License: BSD 3-Clause License
- Repository: https://github.com/theskumar/python-dotenv

#### slowapi (MIT)
- Version: 0.1.9
- Copyright: © Laurent LAPORTE
- License: MIT License
- Repository: https://github.com/laurent-laporte-pro/slowapi

#### pydantic (MIT)
- Version: 2.10.3
- Copyright: © Samuel Colvin and other contributors
- License: MIT License
- Repository: https://github.com/pydantic/pydantic

#### pydantic-settings (MIT)
- Version: 2.6.1
- Copyright: © Samuel Colvin and other contributors
- License: MIT License
- Repository: https://github.com/pydantic/pydantic-settings

#### rapidfuzz (MIT)
- Version: >=3.0
- Copyright: © Max Bachmann
- License: MIT License
- Repository: https://github.com/maxbachmann/RapidFuzz

#### PyJWT (MIT)
- Version: 2.10.1
- Copyright: © José Padilla
- License: MIT License
- Repository: https://github.com/jpadilla/pyjwt

#### python-multipart (Apache-2.0)
- Version: 0.0.20
- Copyright: © Andrew Dunham
- License: Apache License 2.0
- Repository: https://github.com/andrew-d/python-multipart

#### anyio (MIT)
- Version: 4.7.0
- Copyright: © Alex Grönholm
- License: MIT License
- Repository: https://github.com/agronholm/anyio

---

### sidecar/requirements.txt

#### gliner (Apache-2.0)
- Version: 0.2.14
- Copyright: © Urchade Zaratiana et al.
- License: Apache License 2.0
- Repository: https://github.com/urchade/GLiNER
- **Modified**: This project uses GLiNER for NER with custom configurations

```
Apache License 2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

#### presidio-analyzer (MIT)
- Version: 2.2.355
- Copyright: © Microsoft Corporation
- License: MIT License
- Repository: https://github.com/microsoft/presidio

```
MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

#### spacy (MIT)
- Version: 3.8.3
- Copyright: © ExplosionAI GmbH
- License: MIT License
- Repository: https://github.com/explosion/spaCy

```
MIT License

Copyright (c) 2016 ExplosionAI GmbH

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

#### redis (MIT)
- Version: 5.2.0
- Copyright: © Redis Ltd.
- License: MIT License
- Repository: https://github.com/redis/redis-py

#### huggingface_hub (Apache-2.0)
- Version: >=0.23.0
- Copyright: © Hugging Face, Inc.
- License: Apache License 2.0
- Repository: https://github.com/huggingface/huggingface_hub

---

### mcp_server/requirements.txt

#### mcp (MIT)
- Version: 1.3.0
- Copyright: © Anthropic, PBC
- License: MIT License
- Repository: https://github.com/modelcontextprotocol/python-sdk

---

## Docker Images

### python:3.11-slim (PSF)
- License: Python Software Foundation License
- Repository: https://hub.docker.com/_/python

### python:3.12-slim (PSF)
- License: Python Software Foundation License
- Repository: https://hub.docker.com/_/python

### postgres:16-alpine (PostgreSQL License)
- License: PostgreSQL License (MIT-like)
- Repository: https://hub.docker.com/_/postgres

### nginx:alpine (BSD-2-Clause)
- License: BSD 2-Clause License
- Repository: https://hub.docker.com/_/nginx

### redis:7-alpine (BSD-3-Clause)
- Version: 7.x
- License: BSD 3-Clause License (versions < 7.4)
- Repository: https://hub.docker.com/_/redis
- **Note**: Redis 7.4+ uses dual RSALv2/SSPLv1 license. This project uses Redis 7.x
  for local ephemeral storage only. For production deployments, consider using
  Redis < 7.4 or evaluating the license implications.

---

## Machine Learning Models

### GLiNER (urchade/gliner_multi_pii-v1)
- **License**: Apache License 2.0
- **Copyright**: © Urchade Zaratiana et al.
- **Repository**: https://huggingface.co/urchade/gliner_multi_pii-v1
- **Usage**: Named Entity Recognition for PII detection
- **Modified**: Used with custom configurations and thresholds

### spaCy French Model (fr_core_news_md)
- **License**: CC BY-SA 4.0
- **Copyright**: © ExplosionAI GmbH
- **Repository**: https://github.com/explosion/spacy-models
- **Usage**: French NLP pipeline for Presidio
- **Note**: CC BY-SA 4.0 requires attribution and share-alike for derivative works.
  The model is used as-is for NLP processing and is not modified or redistributed.

### Presidio Recognizers
- **License**: MIT
- **Copyright**: © Microsoft Corporation
- **Repository**: https://github.com/microsoft/presidio
- **Usage**: Pattern-based PII detection (IBAN, AVS, phone, email)
- **Modified**: Custom patterns added for Swiss-specific formats (AVS, IBAN CH)

### Qwen (qwen3-pii via Ollama)
- **Base Model**: Qwen2.5 (Apache-2.0)
- **Copyright**: © Alibaba Cloud (base model), © 2026 David Miguel Loureiro Neri (fine-tuned version)
- **Repository**: https://huggingface.co/Qwen
- **Fine-tuned Model**: davidneri/qwen3-pii (Apache-2.0)
- **License**: Apache License 2.0
- **Usage**: Column classification for ambiguous PII detection
- **Modified**: Fine-tuned on modified HuggingFace dataset for PII recognition
- **Documentation**: See [MODEL.md](MODEL.md) for installation and usage

```
Apache License 2.0

Copyright 2023 Alibaba Cloud

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

## Frontend Dependencies

### nginx (BSD-2-Clause)
- License: BSD 2-Clause License
- Repository: https://nginx.org/

No JavaScript or CSS frameworks are used. The frontend consists of vanilla
HTML, CSS, and JavaScript.

---

## License Summary

| Component | License | Commercial Use | Modification | Distribution |
|-----------|---------|----------------|--------------|--------------|
| This project | PolyForm Noncommercial 1.0.0 | ❌ No | ✅ Yes | ✅ Yes (same license) |
| FastAPI | MIT | ✅ Yes | ✅ Yes | ✅ Yes |
| GLiNER | Apache-2.0 | ✅ Yes | ✅ Yes | ✅ Yes |
| Presidio | MIT | ✅ Yes | ✅ Yes | ✅ Yes |
| spaCy | MIT | ✅ Yes | ✅ Yes | ✅ Yes |
| spaCy fr model | CC BY-SA 4.0 | ✅ Yes | ✅ Yes (share-alike) | ✅ Yes (same license) |
| Qwen | Apache-2.0 | ✅ Yes | ✅ Yes | ✅ Yes |
| Redis | BSD-3-Clause | ✅ Yes | ✅ Yes | ✅ Yes |
| PostgreSQL | PostgreSQL License | ✅ Yes | ✅ Yes | ✅ Yes |
| nginx | BSD-2-Clause | ✅ Yes | ✅ Yes | ✅ Yes |

---

## Compliance Notes

### Apache-2.0 Components
For components under Apache-2.0 (GLiNER, asyncpg, huggingface_hub, python-multipart, Qwen):
- State changes made to the original work
- Include a copy of the Apache-2.0 license
- Retain all copyright, patent, trademark, and attribution notices

### CC BY-SA 4.0 Components
For the spaCy French model:
- Provide attribution to ExplosionAI GmbH
- If you modify and redistribute the model, use the same license (CC BY-SA 4.0)
- The model is used as-is in this project and not modified

### MIT Components
For MIT-licensed components:
- Include the copyright notice and permission notice
- The software is provided "as is" without warranty

---

## Reporting License Issues

If you believe any component is incorrectly licensed or if you are a copyright
holder and have concerns, please contact: **david@neri.contact**

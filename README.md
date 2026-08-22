# reference-check-agent

한국 학술지 편집자가 HWP/HWPX 원고의 본문 인용과 참고문헌을 대조하고,
근거가 포함된 수정 요청 문구를 검토하는 웹 서비스다.

## 로컬 실행

기본 결정론 프로파일은 Copilot SDK와 Agent Framework를 설치하지 않는다.

```powershell
python -m pip install -r backend/requirements.txt
$env:PYTHONPATH = "backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

AI 개발 환경만 `backend/requirements-ai.txt`를 사용한다. `AI_ENABLED=false`이면
AI 모듈을 지연 임포트하며 결정론 경로만 실행한다.

## 배포 프로파일

- 기본: `infra/deploy.ps1` — 런타임을 제외하고 결정론 기능 전체를 배포한다.
- AI: `infra/deploy.ps1 -WithRuntime` — 고정된 SDK 1.0.2 Linux wheel의 Copilot
  CLI를 이미지에 포함한다. 실행 중 다운로드는 항상 금지한다.

AI 프로파일의 Python 3.12 wheel 검증에는
`manylinux_2_28_x86_64`와 `manylinux2014_x86_64`가 모두 필요하다. 전자는
Copilot SDK 번들 런타임, 후자는 `pydantic-core` 전이 의존성을 받기 위한 태그다.

## GitHub 토큰 주입과 제거

토큰 값은 저장소, Bicep, 워크플로 입력에 넣지 않는다.

1. Azure Key Vault에 GitHub 토큰을 secret으로 저장한다.
2. `infra/deploy.ps1 -WithRuntime -KeyVaultName <vault> -GitHubTokenSecretUri
   <secret-uri>`로 배포한다.
3. Bicep이 Container App 관리 ID에 Key Vault Secrets User 역할을 부여하고
   `GITHUB_TOKEN`을 secret reference로 주입한다.
4. `/health/ready`에서 AI 활성 여부만 확인한다. 토큰 값이나 일부는 반환하지 않는다.

심사 종료 후 Container App의 `AI_ENABLED`를 `false`로 바꾸고 Key Vault secret을
삭제하거나 비활성화한다. 이후 기본 프로파일로 다시 배포해 런타임도 제거한다.

# finops-agent-skill

Arize AX Managed Agent용 FinOps 스킬입니다. Arize에 기록된 LLM 비용을 조직 구조(팀, 코스트센터, 프로젝트, 스페이스 등)별로 집계해 차지백 리포트를 만들고, 예산 대비 소진율, 세션당·완료 세션당 비용, 열려 있는 Cost Agent 이슈를 함께 보여 줍니다.

모든 수치는 Arize 집계에서 스크립트가 계산합니다. 에이전트는 스크립트 출력을 인용만 하고 직접 계산하거나 추정하지 않습니다.

## 구성

| 경로 | 내용 |
| --- | --- |
| `SKILL.md` | 에이전트가 따르는 지침 (실행 방법, 답변 규칙, 오류 처리) |
| `agent/system_prompt.md` | Managed Agent 시스템 프롬프트 |
| `finops/` | 리포트 엔진 (Python 표준 라이브러리만 사용) |
| `scripts/finops.py` | 리포트 CLI |
| `scripts/setup_widgets.py` | 리포트가 읽는 피벗 위젯을 만드는 1회성 설정 스크립트 |
| `scripts/setup_charts.py` | 일별 비용 차트 대시보드를 만드는 선택 스크립트 |
| `scripts/link_arize_key.py` | 읽기 전용 Arize API 키를 Managed Agent 샌드박스 연동으로 등록 |
| `examples/` | 설정 예시와 조직도·예산 CSV |
| `synthetic/` | 테스트용 합성 트레이스 생성기 |

## 사전 준비

- Python 3.10 이상 (외부 패키지 불필요)
- 트레이스에 비용이 기록되어 있어야 합니다: `llm.cost.total`, `llm.token_count.total`, 세션 단위 집계를 하려면 `session.id`, 사용자별 차지백을 하려면 `user.id`
- 프로젝트를 읽을 수 있는 Arize API 키

## 빠른 시작

### 1. 설정 파일 작성

`examples/acme.json`을 복사해 수정합니다.

- `projects`: 집계할 Arize 프로젝트 (`id`, `name`, `space_id`, `space_name`)
- `dimensions`: 집계 계층. 첫 단계는 `attribute:user.id`, `space`, `project` 중 하나이고, 이후 단계는 CSV 매핑으로 올라갑니다 (예: user → team → cost_center).
- `budgets`: 예산 CSV. 예산을 매기는 집계 단계 이름, `month`, `budget_usd` 열로 구성합니다 (예: `team,month,budget_usd`).
- `tz_offset_hours`: 월 경계를 계산할 시간대 (한국은 `9`)

### 2. 피벗 위젯 생성 (최초 1회)

리포트는 대시보드의 피벗 위젯에서 정확한 집계를 읽습니다. 아래 스크립트가 위젯을 만들고 ID를 설정 파일에 기록합니다.

```bash
export ARIZE_API_KEY=<your-arize-api-key>
python3 scripts/setup_widgets.py --config my-config.json --dry-run   # 생성할 내용 확인
python3 scripts/setup_widgets.py --config my-config.json
```

### 3. 리포트 실행

```bash
python3 scripts/finops.py --config my-config.json --format md
python3 scripts/finops.py --config my-config.json --start 2026-11-01T00:00+09:00 --end 2026-11-15T00:00+09:00
```

- 기본 기간은 설정 시간대 기준 이번 달 1일부터 현재까지입니다.
- `--format json`은 후속 질문용, `--format investigation`은 정기 실행 시 이슈 카드가 포함된 조사 파일용입니다.
- 셀프호스팅 Arize나 다른 리전을 쓰는 경우 `FINOPS_GRAPHQL_URL`로 GraphQL 주소를 지정합니다 (예: `https://<your-arize-host>/graphql`).

## Managed Agent로 배포

1. Arize에서 Custom Managed Agent를 만들고 이 저장소를 스킬로 연결합니다. 시스템 프롬프트는 `agent/system_prompt.md`를 사용합니다.
2. 대시보드 읽기 권한이 있는 **읽기 전용** API 키를 샌드박스 연동으로 한 번 등록합니다. 키는 환경 변수 `FINOPS_READONLY_KEY`나 숨김 입력으로 받고 출력하지 않습니다.

   ```bash
   python3 scripts/link_arize_key.py --account-id <ACCOUNT_ID> --space-id <SPACE_ID>
   ```

3. 에이전트 설정에서 위 연동과 AI Provider를 선택하고, 정기 실행(예: 매주 월요일)을 설정합니다.

샌드박스 안에서는 스크립트가 `https://graphql.sandboxcontroller/graphql`을 호출하며, 프록시가 인증 정보를 주입합니다.

## 테스트

```bash
python3 -m pytest
```

합성 데이터로 직접 시험해 보려면 `synthetic/assistant_gen.py --dry-run`으로 생성 내용을 확인한 뒤 `--send`로 Arize에 전송합니다. `examples/assistant-synthetic.json`이 이 데이터에 맞춘 설정입니다.

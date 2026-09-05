# 야담 스토리 유튜브 자동화 파이프라인

옛이야기·썰·야담 **내러티브 장편 영상**을 만드는 자동화 파이프라인입니다.
현행 편성은 **독립 단편 45분 × 2~3편 옴니버스**(대본 2.5~3.6만자) + 백색소음 아웃트로 —
완성본이 2~3시간이 됩니다. 저장소 이름의 `45m` 이 이 편당 길이를 가리킵니다.
Claude Code에서 **"야담 영상 만들어줘"** 한마디로 시작하면, 대본 집필부터 캐릭터 생성,
스토리보드, 씬 이미지, TTS, CapCut 드래프트까지 자동으로 진행되고 — **사람은 정해진
검수 지점에서만 개입**합니다.

```
대본(SCRIPT) → 캐릭터/배경 생성(ASSET★검수) → 스토리보드 → 씬 이미지
→ TTS(Vrew★수동) → 타이밍 매핑 → 장부 카드 → 훅 클립(VEO)
→ CapCut 드래프트(★마무리 편집) → 훅 결합 → 인트로 → 아웃트로 → 업로드
```

이미지 엔진은 두 가지입니다. `flow`(무료, labs.google 웹세션)와 `gemini`(유료 API) —
**yadam 채널은 `gemini` 를 씁니다**(flow 일일 한도 회피, `settings.json image.engine`).
훅 클립 엔진 `pjn` 은 로컬 5090 서버라 무료입니다. TTS(Vrew)와 렌더(CapCut)는 사람이 합니다.

---

## 1. 처음 한 번만: 설치

상세 절차는 **[docs/setup.md](docs/setup.md)** 를 따라가세요. 요약:

```bash
git clone https://github.com/kkabin24/yadam-45m-base.git
cd yadam-45m-base
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
brew install ffmpeg
cp .env.example .env        # 기본 구성이면 키를 채울 필요 없음
```

추가로 필요한 프로그램: **Chrome**(+버너 구글 계정), **Vrew**, **CapCut 데스크톱**, **Claude Code**.

이미지 생성(flow) 셋업 — 작업할 때마다 데몬이 떠 있어야 합니다:
1. Chrome `chrome://extensions` → 개발자 모드 → `scripts/image/flow_extension/` 로드
2. 버너 계정으로 [labs.google Flow](https://labs.google/fx/ko/tools/flow) 로그인, 탭 유지
3. `python3 scripts/image/flow_token_server.py` 실행(켜둔 채로)
4. 확장 아이콘 → **Connect**

### ★이 저장소에 **없는** 것 — 새 PC에서 따로 채워야 하는 것

저장소에는 **코드로 다시 만들 수 없는 것만** 담았습니다. 아래 셋은 용량이 크거나 비밀정보라
git 밖에 있습니다. 없으면 그 단계에서 멈춥니다.

| 없는 것 | 어디에 두나 | 없으면 |
|---|---|---|
| **수면 노이즈 아웃트로** (42MB~248MB, GitHub 100MB 한도 초과) | `channels/yadam/assets/intro,outro/sleep_noise_{30min,1h,2h,3h}_quiet.mp3` | OUTRO 단계에서 멈춘다 |
| **`.env`** — `GEMINI_API_KEY`(유료 이미지), `PJN_API_KEY`(훅 클립) | 루트 `.env` | 이미지·훅 생성 불가 |
| **YouTube OAuth** — `client_secret.json` | `channels/yadam/config/youtube-api/` | 업로드만 불가(제작은 됨) |

인트로 승인본(`assets/intro/intro_ver3_bgm_sub_lufs128.mp4`)과 화풍 앵커
(`config/style_anchor.png`)는 **저장소에 들어 있습니다** — 둘 다 다시 만들 수 없는 기준물이라
빠지면 화풍이 통째로 달라집니다.

### ★규칙은 코드로 막혀 있다 — 검사기 목록

이 파이프라인의 규칙 대부분은 문서가 아니라 **검사기**로 강제됩니다. 그림을 뽑거나 업로드하기
전에 돌리면 되돌릴 수 없는 실수를 막습니다.

```bash
python3 scripts/script/validate_script.py {S}/chapters --style   # 분량·호흡·문체 밴드
python3 scripts/script/check_density.py    {S}/chapters          # 같은 일감 반복·맨 시간점프
python3 scripts/script/check_sameness.py   {S}/chapters          # 편끼리 같은 느낌인가
python3 scripts/script/check_clarity.py    {S}/chapters          # 귀로 한 번 듣고 잡히는가
python3 scripts/storyboard/check_desc.py   {P}/{편}              # ★그림 뽑기 전 — 구도·표정·캐스팅·배경 고정
python3 scripts/render/check_scenes.py     {P}/{편}              # 흰 액자·레터박스
python3 scripts/script/check_meta.py       {P}                   # ★업로드 전 — 설명문 서식·챕터·해시태그
```

`check_desc.py` 는 인물이 나오는 씬에 구도나 표정이 없으면 **한 장도 생성하지 않고 멈춥니다**
(종료코드 2). 경고만으로는 안 막혀서 게이트로 바꾼 것입니다 — 자세한 사연은 `CLAUDE.md` 에 있습니다.

---

## 2. 영상 만들기: 전체 흐름

레포 루트에서 Claude Code를 열고:

```
야담 영상 만들어줘
```

이후 Claude(story-pd 스킬)가 **파일 존재 여부로 현재 단계를 감지**해서 알아서 진행합니다.
중간에 세션이 끊겨도 다시 "이어서 해줘"라고 하면 하던 단계부터 재개됩니다.

### 사람이 하는 일 vs Claude가 하는 일

| 단계 | Claude가 하는 일 | 사람이 하는 일 |
|---|---|---|
| **SCRIPT** | 소재 스캔·분석, 컨셉/아웃라인 제안, 장(章) 집필 | ★**대화로 함께 만든다** — 소재 승인, 컨셉·아웃라인 피드백, 장 배치마다 줄거리 확인 |
| **ASSET** | 캐릭터 턴어라운드·배경 시트 이미지 생성 | ★**검수 게이트** — 이미지를 보고 OK/수정 지시 (여기서 캐릭터 일관성이 결정됨) |
| **STORYBOARD → 씬 이미지** | 씬 분할, 씬별 이미지 생성 | contact sheet 훑어보고 이상한 씬만 지적 |
| **TTS** | 대본을 Vrew용 텍스트로 내보내기, 결과물 병합·자막 정형화 | ★**Vrew 수동 작업** (아래 3절) |
| **RENDER** | CapCut 드래프트 자동 생성 | ★**CapCut 마무리 편집** (아래 4절) |
| **UPLOAD** | 제목·설명·태그 추출, 비공개 업로드 | meta 확인 (업로드 담당자만) |

★ 표시가 사람 개입 지점입니다. 나머지는 Claude가 결과만 보고합니다.

### 3. Vrew 수동 작업 (TTS 게이트)

Claude가 "Vrew 낭독이 필요합니다"라고 멈추면:

1. `channels/{채널}/projects/{프로젝트}/vrew/` 폴더에 `vrew_script.txt`가 생겨 있습니다.
   (대본이 1만자를 넘으면 `vrew_script_part01.txt`, `part02.txt`… 로 분할됨)
2. Vrew에서 각 파일을 낭독시켜 **음성 mp3 + 자막 SRT**를 내보냅니다.
3. 결과물을 같은 `vrew/` 폴더에 넣습니다 —
   단일 파일이면 이름 자유, 분할이면 파트별로 `narration_01.mp3`/`narration_01.srt` 식으로.
4. Claude에게 "넣었어"라고 하면 병합·자막 정형화부터 자동으로 이어갑니다.

### 4. CapCut 마무리 편집 (RENDER 게이트)

Claude가 CapCut 드래프트를 만들면(`capcut_export.py` 자동 실행):

1. CapCut을 열면(실행 중이었다면 재시작) 프로젝트가 목록 맨 위에 떠 있습니다.
   이미지·오디오·자막이 타임라인에 배치된 상태입니다.
2. 사람이 마무리 편집(효과음, 컷 조정, 인트로 등)을 합니다.
3. mp4로 내보내서 **`channels/{채널}/projects/{프로젝트}/output/`** 에 저장합니다(파일명 자유).
4. 이 mp4가 있어야 UPLOAD 단계로 넘어갈 수 있습니다.

---

## 5. 폴더 구조 — 뭐가 어디 생기나

```
channels/{채널}/
  config/                  # 채널 정체성(그림체·설정) — git 공유
  research/topic_log.json  # 소재 대장(중복 방지) — git 공유
  projects/{프로젝트}/      # ▼ 영상 1편의 작업물 — 전부 로컬(git 제외)
    _script/               #   컨셉·아웃라인·장별 원고
    script.txt  meta.txt   #   완성 대본 + 제목/설명
    characters.json        #   인물 레지스트리 (외모 잠금 텍스트)
    locations.json         #   장소 레지스트리
    assets/                #   턴어라운드·배경 시트 이미지
    storyboard.json        #   씬 설계
    scenes/                #   씬 이미지 (scene_NN.png)
    vrew/                  #   Vrew 주고받는 폴더 (3절)
    _video/                #   TTS·자막·타이밍 중간물
    output/                #   CapCut 마커 + 최종 mp4 + 업로드 결과
scripts/                   # 파이프라인 스크립트 (Claude가 실행)
docs/                      # setup.md(온보딩), flow-image.md(이미지 엔진 상세)
.claude/skills/story-pd/   # Claude 작업 지침(스킬) — 파이프라인의 두뇌
```

새 채널을 만들 때는 `channels/_template/`를 복사해서 시작합니다.

---

## 6. git 규칙 (중요 — 여럿이 한 채널을 굴릴 때)

- **커밋·push 하는 것**: 스크립트/스킬/문서 수정, 채널 `config/`, `research/topic_log.json`
- **절대 커밋하지 않는 것**(gitignore가 막아줌): `projects/` 작업물·미디어, `.env`,
  `youtube-api/`(OAuth 시크릿·토큰), 렌더 결과물
- **소재 중복 방지 절차**:
  1. 작업 시작 전 `git pull` — 다른 사람이 확정한 소재를 받아온다
  2. 소재가 확정되면(Claude가 topic_log.json에 기록) `git add channels/*/research/topic_log.json && git commit && git push`

  이걸 안 하면 다른 프리랜서와 같은 소재로 영상을 만들게 됩니다.

---

## 7. 자주 걸리는 문제

| 증상 | 원인/해결 |
|---|---|
| 이미지 생성이 실패한다 | flow 데몬(`flow_token_server.py`)이 꺼졌거나 Chrome Flow 탭 로그아웃. 데몬 재실행 + 확장 Connect |
| gemini 엔진으로 하라는데 에러 | 의도된 동작 — gemini는 유료라 `.env`에 `GEMINI_API_KEY`를 직접 넣은 사람만 사용 가능. 자동 폴백 없음 |
| Claude가 자꾸 Vrew 파일을 기다린다 | mp3/srt를 `{프로젝트}/vrew/`에 넣었는지, 분할 파트 번호가 맞는지 확인 |
| CapCut에 드래프트가 안 보인다 | CapCut을 완전히 종료 후 재시작 (실행 중엔 목록을 다시 안 읽음) |
| 업로드가 안 된다 | `output/`에 mp4가 있는지 + `youtube-api/` OAuth 셋업(setup.md 4절) 여부 확인 |
| 자막 타이밍이 어긋난다 | `openai-whisper` 미설치면 근사치 폴백으로 동작 — 정밀 모드를 원하면 `.venv/bin/pip install openai-whisper` |

---

## Claude를 위한 안내

이 레포에서 작업하는 Claude는:
- 작업 지침의 우선순위: `CLAUDE.md`(전역 원칙) → `.claude/skills/story-pd/SKILL.md`(단계별 절차·상태 감지).
- 영상 제작 요청이 오면 **story-pd 스킬을 로드**하고 상태 감지 테이블(SKILL.md)대로 현재 단계를 판정한다.
- 렌더는 CapCut export 전용 — mp4를 직접 렌더링하지 않는다. `output/capcut_draft.json` 마커 이후는 사용자 대기 게이트.
- 유료 API(gemini·elevenlabs)는 사용자가 settings.json과 .env로 명시 지정했을 때만. 임의 폴백 금지.
- 이 README는 사람용 개요다 — 세부 규칙이 충돌하면 SKILL.md가 우선.

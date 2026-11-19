# ZoroSpoiler

Chrome에서 유튜브 일반 댓글을 시청 진도에 맞춰 가리는 확장 프로그램입니다. 조건을 충족한 영상의 댓글 본문은 먼저 가려집니다. Brev에서 실행되는 `trillionlabs/Zoro-1`이 일반 댓글을 판정하면 바로 열리고, 스포일러는 내용이 공개되는 구간을 재생한 뒤 열립니다. 가려진 댓글은 직접 열 수 있습니다.

## 적용 조건

- 데스크톱 유튜브의 일반 동영상 페이지에서 영상 길이가 **10분 이상 20분 이하**여야 합니다.
- 서버에 해당 영상 ID, 한국어 시간 자막(SRT 또는 VTT), 제작자가 제공한 영상 타임스탬프를 등록합니다. 타임스탬프 파일에는 `0:00` 이후 구간이 하나 이상 있어야 합니다.
- 유튜브 댓글창의 댓글 수가 **30개 이상**이어야 합니다. 댓글 수가 표시되지 않는 경우 확장이 실제로 확인한 댓글 수가 기준에 도달하면 시작합니다.

유튜브의 스크립트 화면은 자막을 시청자에게 보여 줍니다. 공식 자막 다운로드 API를 사용하려면 영상 편집 권한이 필요합니다. 파일럿에는 사용 권한이 있는 자막과 타임스탬프를 등록합니다. [유튜브 스크립트 도움말](https://support.google.com/youtube/answer/15930243?hl=ko) · [자막 다운로드 API](https://developers.google.com/youtube/data/reference/rest/v3/captions/download)

## 실행

### 1. Brev에서 Zoro-1과 앱 서버 실행

Zoro-1의 공식 typed decision 서버가 인스턴스의 `127.0.0.1:30001`에서 실행 중이어야 합니다. 새 인스턴스라면 Hugging Face에서 모델 접근 권한을 확보하고, 모델 카드의 `trida-stack` 설치 및 `serve.py --mode decide` 절차를 따릅니다. [Zoro-1 모델 카드](https://huggingface.co/trillionlabs/Zoro-1#using-the-typed-decision-head)

같은 Brev 인스턴스에서 Python 3.10 이상으로 앱 서버를 실행합니다.

```sh
git clone https://github.com/AllForJoon/zoroSpoiler.git
cd zoroSpoiler
python3 -m server serve
```

### 2. 영상 등록

서버를 실행한 체크아웃에서 다음 명령을 실행합니다. `--duration`은 초 단위입니다. 예시의 영상 ID, 제목, 길이와 파일 경로를 실제 값으로 바꿉니다.

```sh
python3 -m server register \
  --video-id AbCdEf12345 \
  --title '추리 영상 예시' \
  --duration 780 \
  --language ko \
  --captions /path/to/ko.vtt \
  --timestamps /path/to/chapters.txt
```

타임스탬프 파일 예시입니다.

```text
0:00 오프닝
2:30 첫 번째 단서
7:45 사건 재구성
11:20 마지막 장면
```

### 3. 브라우저 연결

브라우저를 실행하는 컴퓨터에서 이 저장소를 받아 `chrome://extensions`의 **개발자 모드 → 압축해제된 확장 프로그램을 로드합니다**로 `extension/`을 선택합니다. 같은 컴퓨터에서 아래 터널을 열고 유지합니다.

```sh
brev port-forward l40s-twodrone-instance --port 8876:8876
```

등록한 영상의 유튜브 페이지를 엽니다. 댓글이 로드되면 보호 상태를 확인할 수 있습니다. 확장 프로그램을 설치하기 전에 열어 둔 탭은 새로고침합니다. [Chrome 확장 프로그램 로드](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked) · [Brev 포트 포워딩](https://docs.nvidia.com/brev/cli/connectivity#brev-port-forward)

## 판정과 재생 진도

화면에 로드된 댓글은 최대 8개씩 판정합니다. 서버는 등록한 영상의 자막을 60초 구간으로 나누고, 댓글과 관련된 구간을 Zoro-1의 선택형 질문에 넣습니다. 시청 진도는 브라우저 안에서 관리하고, 판정 결과를 재사용해 재생 중 가림 상태만 갱신합니다. 앞으로 건너뛴 구간은 실제로 재생하기 전까지 가림이 유지됩니다. 보호가 켜진 뒤 판정이 불확실하거나 실패하면 해당 댓글을 직접 열 수 있습니다.

확장 프로그램은 댓글 식별자와 본문을 SSH 터널을 통해 Brev로 전달합니다. 영상 등록 파일은 서버의 로컬 데이터 디렉터리에 저장합니다.

```sh
python3 -m unittest discover
```

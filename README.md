# ZoroSpoiler

데스크톱 Chrome에서 유튜브 동영상의 스포일러 댓글을 가리는 확장 프로그램입니다. **유튜브에서 스크립트(자막)를 불러올 수 있는 동영상에 적용됩니다.** 일반 동영상 페이지를 열면 확장이 영상의 스크립트를 읽고 댓글을 자동으로 판정합니다.

## Chrome에 설치

1. 저장소를 받습니다.

   ```sh
   git clone https://github.com/AllForJoon/zoroSpoiler.git
   ```

2. Chrome 주소창에서 `chrome://extensions`를 열고 **개발자 모드**를 켭니다. **압축해제된 확장 프로그램을 로드합니다**를 눌러 `zoroSpoiler/extension` 폴더를 선택합니다.
3. ZoroSpoiler 카드의 **세부정보 → 확장 프로그램 옵션**에서 **Brev의 Zoro-1** 또는 **로컬 Zoro-1**을 선택하고 아래 연결을 준비합니다.
4. 유튜브 동영상을 엽니다. 이미 열어 둔 탭은 새로고침합니다. 저장소를 업데이트한 뒤에는 `chrome://extensions`에서 ZoroSpoiler 카드의 새로고침 버튼을 눌러 확장도 다시 로드합니다.

## 모델 연결

### Brev의 Zoro-1

기본 설정입니다. Brev 인스턴스에서 Zoro-1의 typed decision 서버를 `127.0.0.1:30001`로 실행합니다. 같은 인스턴스의 저장소에서 Python 3.10 이상으로 앱 서버를 시작합니다.

```sh
python3 -m server serve
```

Chrome이 실행되는 컴퓨터에서 포트 포워딩을 열고 유지합니다.

```sh
brev port-forward l40s-zoro-spoiler --port 8876:8876
```

[Zoro-1 모델 카드와 서버 실행 방법](https://huggingface.co/trillionlabs/Zoro-1#using-the-typed-decision-head) · [Brev 포트 포워딩](https://docs.nvidia.com/brev/cli/connectivity#brev-port-forward)

### 로컬 Zoro-1

Chrome과 같은 컴퓨터에서 실행합니다. Hugging Face의 [Zoro-1 모델 페이지](https://huggingface.co/trillionlabs/Zoro-1)에서 파일 접근 권한을 받은 뒤, 저장소에서 아래 명령을 실행합니다.

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r server/requirements-local.txt
hf auth login
python3 -m server serve --local-model
```

앱 서버가 포트 `8878`에서 실행되면 Chrome 확장 옵션에서 **로컬 Zoro-1**을 선택합니다. 공식 가중치를 이미 다운로드했다면 `--local-model --checkpoint /path/to/snapshot`으로 해당 파일을 사용할 수 있습니다. 별도 HTTP 방식의 Zoro-1 판정 서버를 이미 실행 중이라면 `python3 -m server serve --port 8878 --zoro-url http://127.0.0.1:30001/v1/decide`로 연결할 수 있습니다.

## 댓글 가리기와 보기

- 영상마다 **스포일러 댓글 가리기** 상태로 시작합니다. 스크립트와 댓글이 로드되면 일반 댓글은 표시하고, 스포일러로 판정된 댓글과 근거가 불충분한 댓글은 가립니다.
- 댓글창 맨 위의 **스포일러 댓글 보기**를 누르면 한꺼번에 표시합니다. **스포일러 댓글 가리기**를 다시 누르면 가림이 복원됩니다.
- 댓글 옆의 **스포일러 보기**와 **스포일러 다시 가리기**로 하나씩 바꿀 수 있습니다. 긴 댓글에도 이 버튼을 YouTube의 **더보기** 바깥에 표시합니다.
- 스크립트가 없는 영상에서는 댓글을 그대로 표시합니다. 연결 오류가 나면 댓글창 상단에 **다시 시도**가 나타납니다.

확장은 시청 중인 영상의 자막과 페이지에 로드된 댓글 본문을 앱 서버로 보냅니다. Brev를 선택하면 데이터가 포트 포워딩을 통해 인스턴스로 전달됩니다. 앱 서버는 자막 근거로 댓글을 판정하고 상태를 반환합니다. 댓글에 회차가 명시되면 해당 회차로 확인된 자막에서만 공개 근거를 찾습니다.

## 테스트

```sh
python3 -m unittest discover
```

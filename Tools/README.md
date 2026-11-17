# Tools — 소리·시간표 도구

파이썬 3.10+, numpy, scipy만 쓴다(그림은 matplotlib가 있을 때만). mp3 등 WAV가 아닌 음원은 ffmpeg로 읽는다(`PATH`, `~/.local/bin/ffmpeg`, 또는 `FFMPEG=/경로`). 모든 명령은 저장소 루트에서 실행한다.

## 실제 곡이 오면 (한 줄)

```bash
python3 Tools/beatmap/run_all.py "<곡 파일>" --credit "Music: <제목> - <아티스트> (<라이선스>)" --plot
```

- 곡을 `Assets/Fireworks/Audio/Music/`에 두고, 분석 → 연출 → 검증을 한 번에 해서 `Assets/Fireworks/Show/cues.json`을 만든다. 검증에 실패하면 종료 코드 1.
- WAV·OGG는 그대로 복사한다. mp3·m4a·flac 등은 Ogg Vorbis(q6, 표지 그림 제거)로 바꿔 두고 분석도 그 OGG로 한다. mp3는 디코더마다 앞 지연(인코더 지연) 처리가 달라 시간축이 수십 ms 어긋날 수 있어서다. 주어진 파일이 프로젝트 안 Audio/Music의 사본이면 변환 뒤 지우고(.meta 포함), 프로젝트 밖 원본은 건드리지 않는다. 그대로 두려면 `--keep-format`.
- 중간 결과: `Tools/out/<곡>_analysis.json`, `Tools/out/cues_timeline.png`(에너지·구간·터짐 밀도·종류별 cue·발사 위치).
- 템포를 잘못 잡으면 `--bpm 128`, 구간 경계가 틀리면 `--boundaries 16.3,32.3,56.3`(각 구간 시작 초, 마디에 맞춰 붙음), 구간 이름이 마음에 안 들면 `--labels intro,build,chorus,...`(구간 수만큼, 순서대로), 다른 배치를 보고 싶으면 `--seed 2`.

### 슬로 번(앞은 아껴 두고 하이라이트에서 터뜨리기) — 예: Free Bird

```bash
python3 Tools/beatmap/run_all.py "<곡 파일>" --credit "Music: Free Bird - Lynyrd Skynyrd" --style slowburn --plot
```

- 템포 변화는 자동으로 찾는다(느린 부분과 빠른 부분의 박을 따로 추적해 하나의 박 목록으로 잇는다). 자동이 틀리면 `--tempo-split 290`(초, 여러 개면 쉼표), 템포를 직접 주려면 `--bpm 60,146`(템포 구간마다 하나). `--tempo-split none`은 자동 감지를 끈다.
- drop(하이라이트)은 기본으로 템포 변화 지점, 없으면 에너지가 가장 크게 뛰는 구간 경계로 잡는다. 그 순간에 타격이 없으면(늘인 보컬·필인) 4초 안의 첫 강한 타격으로 옮긴다. 직접 정하려면 `--drop 97.4`.
- 마지막 최고조는 끝부분의 가장 강한 타격이다. 곡이 페이드아웃으로 끝나면(라디오 편집판) 페이드가 시작되기 직전의 마지막 강박에 둔다. 직접 정하려면 `--climax 246.4`.
- 템포가 서서히 빨라지는 부분(솔로 가속)은 국소 템포 곡선을 따라 박을 추적한다.
- drop 전: 혜성·단발 버들·작은 링만 드문드문, 후렴마다 조금씩 늘고, 마지막 몇 마디는 혜성이 빨라지며 올라간 뒤 1~2박을 비운다.
- drop 분출(약 2마디, 국소 템포 기준): 하늘 전체 8발 + hit로 시작해 박마다 빠짐없이 큰 국화·모란·크로세트 대칭 사격이 좌우 끝까지 퍼지고(초당 8~10발), 마지막 박에 금색 버들 커튼 7발. 박마다 몇 발인지는 '1초 12발' 안에서 빈 박이 없도록 미리 계산한다(첫 박을 8보다 키우면 다음 1초에 한 쌍도 못 넣어 구멍이 생긴다).
- drop 뒤: 20~40초 단계마다 주력 종류·색·발사 패턴을 바꾸며 계단식으로 키운다. 첫 단계부터 초당 4발 이상이고 단계마다 올라간다. 박 위에만 쏘면서 '1초 6발' 상한을 지키면 140 BPM에서 초당 4.7발, 150 BPM에서 5.0발이 천장이라 계단 폭은 작다. 크게 뛰는 것은 finale(상한 12)이다. 단계 사이에 한 박을 비워 숨을 고르고, 마지막 큰 타격에 금색 버들 커튼, 여운, 정적.
- cue JSON에는 계약 밖 추가 필드 `style`, `drop`, `drop_end`(분출 끝), `tempo_map`(템포 구간별 BPM)이 붙는다(Unity는 무시). 최상위 `bpm`은 가장 긴 템포 구간의 값이다.
- 상한: 1초 창에 공중 cue 6개(finale와 drop 분출 `drop`~`drop_end` 동안은 12개), 동시에 살아 있는 불꽃 부하(크기 합) 48(Unity 실측 부하 38 ≈ 입자 1.9만 개 기준 약 2.4만 개, 목표 3만 이하).

## 개별 명령

| 명령 | 하는 일 |
|---|---|
| `python3 Tools/sfx/make_sfx.py [--plot]` | 효과음 14개 합성 → `Assets/Fireworks/Audio/SFX/` (44.1 kHz, 16-bit, mono). `--plot`이면 `Tools/out/sfx_spectrograms.png` |
| `python3 Tools/make_test_song.py [--song slowburn]` | 시험곡 → `Assets/Fireworks/Audio/Music/test_song.wav` + 정답 `Tools/out/test_song_truth.json`. `--song slowburn`이면 60→140 BPM 시험곡 `test_song_slowburn.wav`(약 5분) + `test_song_slowburn_truth.json` |
| `python3 Tools/beatmap/analyze.py <음원> -o <analysis.json> [--bpm N 또는 60,140] [--tempo-split S]` | 길이, 템포 구간별 BPM, 박, 마디 첫 박, 온셋·RMS 곡선(10 Hz), 구간 경계와 에너지, 강한 타격 |
| `python3 Tools/beatmap/choreograph.py <analysis.json> -o <cues.json> --music <파일명> --credit "<문구>" [--style slowburn --drop S --climax S] [--seed N] [--labels ...] [--boundaries ...]` | 계약 형식의 cue JSON |
| `python3 Tools/beatmap/validate_cues.py <cues.json> [--analysis <analysis.json>] [--music-dir <폴더>]` | 계약 검사. 문제 있으면 종료 코드 1 |
| `python3 Tools/beatmap/compare_truth.py <analysis.json> <truth.json> [--cues <slowburn cues>]` | 분석 결과를 시험곡 정답과 비교(표 출력, 템포 변화·drop 포함) |
| `python3 Tools/beatmap/plot_timeline.py <analysis.json> <cues.json> -o <png>` | 타임라인 그림 |

시험곡 전체 흐름 재현:

```bash
python3 Tools/make_test_song.py
python3 Tools/beatmap/run_all.py Assets/Fireworks/Audio/Music/test_song.wav \
  --credit "Music: Test Track (generated)" --out Assets/Fireworks/Show/cues_test.json --plot
python3 Tools/beatmap/compare_truth.py Tools/out/test_song_analysis.json Tools/out/test_song_truth.json

# 템포 변화 + 슬로 번
python3 Tools/make_test_song.py --song slowburn
python3 Tools/beatmap/run_all.py Assets/Fireworks/Audio/Music/test_song_slowburn.wav \
  --credit "Music: Slow-burn Test Track (generated)" --style slowburn --out Assets/Fireworks/Show/cues_test_slowburn.json --plot
python3 Tools/beatmap/compare_truth.py Tools/out/test_song_slowburn_analysis.json Tools/out/test_song_slowburn_truth.json \
  --cues Assets/Fireworks/Show/cues_test_slowburn.json
```

## 알아 둘 것

- 같은 입력과 같은 `--seed`면 결과가 바이트 단위로 같다. 효과음·시험곡도 시드가 고정이다.
- 효과음은 볼륨이 미리 섞여 있다(피크: boom −1.1, pop −4, launch −6, crackle −7, fountain −13, whistle −15 dBFS). Unity에서 모두 볼륨 1.0으로 시작하면 된다. `fountain_loop.wav`는 처음과 끝이 이어지게 만들었다(루프 켜기).
- 박자 모델은 4/4로 가정한다. 3/4·변박 곡은 마디 첫 박이 틀릴 수 있다.
- 느린 부분은 실제 템포의 2배(8분음표 박)로 잡힐 수 있다. 박 목록에 진짜 박이 모두 들어 있어 연출에는 지장이 없지만, 정확한 값이 필요하면 `--bpm 60,140`처럼 준다.
- 연출 규칙(밀도·패턴·상한)은 `Tools/beatmap/choreograph.py` 맨 위 설명, 계약 값은 `Tools/beatmap/contract.py`에 한곳으로 모여 있다.

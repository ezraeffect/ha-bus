# TAGO Bus Location (Home Assistant)

국토교통부 TAGO 버스위치정보 API로 시내버스의 실시간 위치를 Home Assistant 지도에 표시하는 커스텀 통합입니다.
천안(34010)을 기본값으로 두었지만, TAGO에 등록된 다른 시·군 버스도 쓸 수 있습니다.

- 운행 중인 버스 한 대마다 `geo_location` 엔티티를 만들고, 운행이 끝나면 자동으로 지웁니다.
- 지도 마커에 노선 번호를 표시하고, 방향마다 색을 다르게 칠합니다. (파랑·주황·초록·보라)
- 버스 속성: 차량번호, 방향(○○ 방면), 현재 정류장, 다음 정류장, 집까지 거리(엔티티 상태값, km)
- 운행 대수 센서: 전체 대수와 방향별 대수
- 운행 중인 버스가 없으면 조회 주기를 5분으로 늘려 호출량을 아낍니다.

## 1. API 키 발급

1. [공공데이터포털](https://www.data.go.kr)에 로그인합니다.
2. 아래 두 API에 각각 **활용신청**을 합니다. 개발계정은 자동 승인됩니다.
   - [국토교통부_(TAGO)_버스위치정보](https://www.data.go.kr/data/15098533/openapi.do)
   - [국토교통부_(TAGO)_버스노선정보](https://www.data.go.kr/data/15098529/openapi.do)
3. 마이페이지에서 **일반 인증키**를 복사합니다. Decoding 키와 Encoding 키 중 아무거나 써도 됩니다.

> 발급 직후에는 키가 활성화될 때까지 1~2시간 걸릴 수 있습니다. 그 사이에는 "인증키가 올바르지 않습니다" 오류가 나옵니다.

## 2. HACS로 설치

1. HACS → 오른쪽 위 ⋮ → **사용자 지정 저장소**를 엽니다.
2. `https://github.com/ezraeffect/ha-bus`를 입력하고 유형은 **Integration**으로 고릅니다.
3. **TAGO Bus Location**을 설치한 뒤 Home Assistant를 재시작합니다.

HACS 없이 설치하려면 `custom_components/tago_bus` 폴더를 HA 설정 폴더의 `custom_components/` 아래에 복사하면 됩니다.

## 3. 설정

**설정 → 기기 및 서비스 → 통합 구성요소 추가 → TAGO Bus Location**

1. 인증키를 입력합니다.
2. 도시로 `천안시 (34010)`를 고르고, 노선 번호에 `5`를 입력합니다.
3. 검색된 노선 목록이 기본으로 모두 선택되어 있습니다. 상행과 하행이 따로 등록된 노선이라면 둘 다 선택된 채로 두면 됩니다.

조회 주기는 통합의 **구성**에서 바꿀 수 있습니다. 기본값은 30초입니다.
다른 노선을 추가하려면 통합을 한 번 더 추가하세요. 인증키는 자동으로 채워집니다.

## 4. 지도 카드

```yaml
type: map
title: 천안 5번 버스
geo_location_sources:
  - tago_bus
hours_to_show: 0
```

집 위치나 다른 엔티티도 함께 보려면 `entities:` 항목에 추가하세요.

## 자동화 예시: 버스가 정류장 근처에 오면 알림

먼저 정류장 주변에 zone을 만듭니다. (예: `zone.du_jeong_yeog`)

```yaml
triggers:
  - trigger: geo_location
    source: tago_bus
    zone: zone.du_jeong_yeog
    event: enter
actions:
  - action: notify.notify
    data:
      message: >
        {{ trigger.to_state.attributes.route_no }}번 버스
        ({{ trigger.to_state.attributes.direction }})가 곧 도착합니다.
```

## 호출량 참고

API를 한 번 조회할 때마다 선택한 노선 수만큼 호출합니다.
예를 들어 노선 2개를 30초 간격으로 조회하면 시간당 약 240회입니다.
개발계정에는 일일 호출 한도가 있으니, 한도를 초과하면 조회 주기를 늘리세요.

## 개발

```bash
python -m unittest discover -s tests -v
```

HACS 기본 저장소 목록에 등록하려면 저장소에 설명(description)과 토픽이 있어야 합니다.

데이터 출처: 국토교통부 국가대중교통정보센터(TAGO), 공공데이터포털

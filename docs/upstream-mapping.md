# Upstream Mapping

This file records which upstream implementation details were carried into the Python framework.

## BIT101-Android

Useful source files:

- `data/src/main/java/cn/bit101/android/data/repo/DefaultDDLScheduleRepo.kt`
  - Gets the Lexue index page, extracts `sesskey`, posts to `/calendar/export.php`, fetches the generated `.ics` URL.
- `api/src/main/java/cn/bit101/api/service/school/SchoolLexueApiService.kt`
  - Defines the Lexue endpoints:
    - `GET /`
    - `POST /calendar/export.php`
    - `GET <calendar-url>`
- `api/src/main/java/cn/bit101/api/converter/lexue/GetIndexConvertFactory.kt`
  - Extracts `sesskey` with a regex from the Lexue index HTML.
- `api/src/main/java/cn/bit101/api/converter/lexue/GetCalendarUrlConvertFactory.kt`
  - Finds the generated subscription URL from `.calendarurl`.
- `api/src/main/java/cn/bit101/api/converter/lexue/GetCalendarConvertFactory.kt`
  - Maps `UID`, `SUMMARY`, `DESCRIPTION`, `CATEGORIES`, and `DTSTART` into DDL events.
- `features/schedule/.../DDLScheduleViewModel.kt`
  - Preserves existing completion state by UID when Lexue events are refreshed.
- `data/src/main/java/cn/bit101/android/data/common/AESUtils.kt`
  - SSO page-login password encryption: Base64 decode `login-croypto`, then AES/ECB/PKCS5Padding.

Ported modules:

- `lexue_attention.lexue`
- `lexue_attention.ics`
- `lexue_attention.models`
- `lexue_attention.auth.encrypt_sso_password`

### Current SSO v4 flow

- Upstream: `BIT101-Android` `api/.../SchoolLexueService.kt`, using `BIT-Login v4.0.2`.
- Password login: `GET /cas/login`, captcha preflight, AES-encrypted password, and USTC risk fields when requested.
- Password second factor: encrypted bound-phone lookup, `sendSmsCode`, `checkToken`, then `smsLogin` form submission.
- Local implementation: `lexue_attention.auth.BitSsoV4Client`.
- AstrBot interaction: `/lexue login` waits for the SMS code through AstrBot `session_waiter` and persists only the exported Lexue iCalendar URL.

## BIT-Login

Useful source files:

- `bit_login/login.py`
  - Uses `https://sso.bit.edu.cn/cas/v1/tickets` to get a TGT and service ticket.
- `bit_login/service.py`
  - Keeps a `requests.Session` as the reusable authenticated transport.
- `bit_login/utils.py`
  - Converts normal internal URLs to BIT WebVPN URL format.

Ported modules:

- `lexue_attention.auth.BitSsoTicketClient`
- `lexue_attention.webvpn`

## Boundaries

The core package does not send QQ messages, emails, or AstrBot events. Those should call the core package through a thin adapter after the fetching, parsing, state, and reminder rules are stable.

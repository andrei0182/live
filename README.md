# live

Alerte live pe Telegram pentru pick-urile modelului de tenis din
[andrei0182/tenis](https://github.com/andrei0182/tenis).

## Sursa de date

Același API Superbet pe care îl folosește deja `tenis_scraper/events.py`, cu
`index=active-live` în loc de `active-prematch`. Verificat pe 2026-10-17:

| Ce | Unde în răspuns |
|---|---|
| seturi câștigate | `inplay_stats.home_team_score` / `away_team_score` |
| game-uri pe set | `inplay_stats.periods[]` (`num` = setul) |
| puncte în game-ul curent, cine servește | `home_team_game_score` / `away_team_game_score`, `team_serving` |
| stare | `inplay_stats_metadata.status` (`STARTED` / `FINISHED`), `period_status` (`S1`, `S2`…) |
| cote live câștigător | market `Final` (id 521), `status` 1 = activă, 2 = suspendată |

Piețele extinse (total game-uri pe jucător id 999/996, total pe meci id 1002,
total pe set id 524, handicap id 520…) vin, atât pre-meci cât și live, prin
endpoint-ul SSE `v3/subscription/ro-RO/events?events=<id>`, deja folosit în
`tenis`. TennisExplorer nu e necesar.

## Regulile

Pentru fiecare pick `pending` de azi/ieri din `stats/picks_log.csv`, cu jucătorul
recomandat favorit în model (`comp_pct >= MIN_MODEL_PCT`, implicit 50):

1. **Set 1 pierdut**: favoritul a pierdut setul 1, e încă în urmă la seturi și
   cota lui live >= `max(rec_odds × ODDS_FACTOR, MIN_ODDS)` (implicit 1.5 și 1.8).
   Regula se închide dacă favoritul câștigă setul 1 sau egalează la seturi.
2. **Linia de game-uri**: linia principală live la „Total game-uri <favorit>”
   scade cu cel puțin `GAMES_DROP` game-uri (implicit 2) sub linia principală
   pre-meci. Linia principală e cea cu cotele Sub/Peste cele mai apropiate.
   Linia pre-meci se salvează la fiecare rulare cât meciul nu a început, deci
   referința e ultima citire dinainte de start. Dacă monitorul nu a apucat să
   vadă meciul înainte de start, regula nu se aplică pe acel meci.

Fiecare regulă trimite cel mult o alertă pe pick. Monitorul nu mai urmărește un
pick după ce ambele reguli s-au închis sau meciul s-a terminat.

## Configurare

1. Creează un bot cu [@BotFather](https://t.me/BotFather) și copiază tokenul.
2. Trimite-i botului un mesaj, apoi deschide
   `https://api.telegram.org/bot<TOKEN>/getUpdates` și copiază `chat.id`.
3. În repo: *Settings → Secrets and variables → Actions*:
   - secrete: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`;
   - opțional, variabile: `ODDS_FACTOR`, `MIN_ODDS`, `MIN_MODEL_PCT`, `GAMES_DROP`.
4. *Actions → Live tennis alerts → Run workflow* cu „Trimite doar un mesaj de
   test” bifat, ca să verifici botul.

Workflow-ul pornește la fiecare 20 de minute (minutele 2, 22, 42) și urmărește
meciurile cel mult 18 minute, cu o verificare pe minut. Dacă niciun pick nu e live și niciunul nu
începe în fereastra respectivă, se oprește imediat. Cron-ul GitHub poate întârzia
câteva minute.

## Local

```bash
pip install -r requirements.txt
pytest
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python live_monitor.py --test-telegram
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python live_monitor.py --max-minutes 180
```

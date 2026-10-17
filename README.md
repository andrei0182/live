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

Piețele extinse live (total game-uri id 1002, total pe set id 524, handicap id 520…)
vin prin endpoint-ul SSE `v3/subscription/ro-RO/events?events=<id>`, deja folosit
în `tenis`. Acum nu sunt folosite, dar sunt disponibile pentru o regulă pe linia
de game-uri. TennisExplorer nu e necesar.

## Regula

Pentru fiecare pick `pending` de azi/ieri din `stats/picks_log.csv`:

- jucătorul recomandat e favorit în model (`comp_pct >= MIN_MODEL_PCT`, implicit 50);
- a pierdut setul 1 și e încă în urmă la seturi;
- cota lui live >= `max(rec_odds × ODDS_FACTOR, MIN_ODDS)` (implicit 1.5 și 1.8).

Fiecare pick primește cel mult o alertă. Monitorul nu mai urmărește un pick
după ce favoritul câștigă setul 1, egalează la seturi sau meciul se termină.

## Configurare

1. Creează un bot cu [@BotFather](https://t.me/BotFather) și copiază tokenul.
2. Trimite-i botului un mesaj, apoi deschide
   `https://api.telegram.org/bot<TOKEN>/getUpdates` și copiază `chat.id`.
3. În repo: *Settings → Secrets and variables → Actions*:
   - secrete: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`;
   - opțional, variabile: `ODDS_FACTOR`, `MIN_ODDS`, `MIN_MODEL_PCT`.
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

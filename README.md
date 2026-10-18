# live

Alerte live pe Telegram pentru pronosticurile din
[andrei0182/pariuri](https://github.com/andrei0182/pariuri):

- `tennis/stats/model90_log.csv`: „Model 90%” (probabilitatea în `probability`, ca fracție);
- `football/recommendations_log.csv`: recomandările de fotbal Peste 2.5 (doar cele
  `pending`, fără `excluded`).

Raportul zilnic de tenis (`picks_log.csv`) nu se joacă, deci nu e urmărit.
Un meci prezent pe două piețe în Model 90% se urmărește o singură dată.

## Sursa de date

Același API Superbet pe care îl folosesc deja scraperele de tenis, cu
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
endpoint-ul SSE `v3/subscription/ro-RO/events?events=<id>`, deja folosit de
scraperele de tenis. TennisExplorer nu e necesar.

## Regulile

Pentru tenis, la fiecare pick Model 90% `pending` de azi/ieri, cu
probabilitatea >= `MIN_MODEL_PCT` (implicit 50):

1. **Set 1 pierdut**: favoritul a pierdut setul 1, e încă în urmă la seturi și
   cota lui live >= `max(rec_odds × ODDS_FACTOR, MIN_ODDS)` (implicit 1.5 și 1.8).
   La pick-urile Model 90% (cote ~1.00–1.15) pragul e practic `MIN_ODDS`.
   Regula se închide dacă favoritul câștigă setul 1 sau egalează la seturi.
2. **Linia de game-uri**: linia principală live la „Total game-uri <favorit>”
   scade cu cel puțin `GAMES_DROP` game-uri (implicit 2) sub linia principală
   pre-meci. Linia principală e cea cu cotele Sub/Peste cele mai apropiate.
   Linia pre-meci se salvează la fiecare rulare cât meciul nu a început, deci
   referința e ultima citire dinainte de start. Dacă monitorul nu a apucat să
   vadă meciul înainte de start, regula nu se aplică pe acel meci.

3. **Fotbal, 0-0 în prima repriză**: meciul cu pronostic Peste 2.5 e încă 0-0 în
   prima repriză (`event_status_label` = `1H`) din minutul `FH_MINUTE` (implicit 25).
   Alerta are cotele live la „Prima repriză - Total goluri” Peste 0.5 și „Total
   goluri” Peste 2.5. Regula se închide la primul gol sau la finalul reprizei.
   Meciul se urmărește de la ora de start din log până la ~55 de minute după.
   Echipele se potrivesc după cuvintele specifice din nume (cel puțin jumătate pe
   fiecare echipă, aceeași categorie juniori/fete), alegând meciul cel mai apropiat.

Fiecare regulă trimite cel mult o alertă pe pick. Monitorul nu mai urmărește un
pick după ce ambele reguli s-au închis sau meciul s-a terminat.

## Postările zilnice

Monitorul publică singur, la prima rulare de după ora setată, o dată pe zi:

- **dimineața** (`POST_MORNING`, implicit 09:00): programul zilei (doar pronosticurile jucate:
  tenis Model 90% cu ora de start și fotbal Peste 2.5) și rezultatele alertelor de ieri care nu se terminaseră seara;
- **seara** (`POST_EVENING`, implicit 23:30): fiecare alertă a zilei cu rezultatul
  ei (✅/❌/⏳) și rezultatele pronosticurilor zilei; duminica și bilanțul săptămânii.

Rezultatul unei alerte: 🎾 set 1 = favoritul a câștigat meciul; 📉 game-uri =
favoritul a făcut mai multe game-uri decât linia live din alertă; ⚽ = s-a marcat
în prima repriză. Rezultatele vin din indexul `live` al Superbet, care păstrează
și meciurile terminate. `POSTS=off` oprește postările.

## Configurare

1. Creează un bot cu [@BotFather](https://t.me/BotFather) și copiază tokenul.
2. Trimite-i botului un mesaj, apoi deschide
   `https://api.telegram.org/bot<TOKEN>/getUpdates` și copiază `chat.id`.
3. În repo: *Settings → Secrets and variables → Actions*:
   - secrete: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`;
   - opțional, variabile: `ODDS_FACTOR`, `MIN_ODDS`, `MIN_MODEL_PCT`, `GAMES_DROP`, `FH_MINUTE`,
     `POST_MORNING`, `POST_EVENING`, `POSTS`.
4. *Actions → Live tennis alerts → Run workflow* cu „Trimite doar un mesaj de
   test” bifat, ca să verifici botul.

Workflow-ul pornește la fiecare 20 de minute (minutele 7, 27, 47) și urmărește
meciurile cel mult 18 minute (o rulare manuală poate alege 60, 120 sau 180 de
minute din *Run workflow*), cu o verificare pe minut. Dacă niciun pick nu e live și niciunul nu
începe în fereastra respectivă, se oprește imediat. Cron-ul GitHub poate întârzia
câteva minute.

## Link din alertă

Alerta conține linkul spre meci pe site (`superbet.ro/cote/tenis/<id>`).
Testat pe Android (2026-10-17): aplicația Superbet nu acceptă linkuri spre un
meci anume, iar nici Chrome, nici Google Play nu o pornesc dintr-o pagină web,
deci în aplicație meciul se caută după numele jucătorilor din alertă.

## Local

```bash
pip install -r requirements.txt
pytest
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python live_monitor.py --test-telegram
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python live_monitor.py --max-minutes 180
```

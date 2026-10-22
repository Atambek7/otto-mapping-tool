# OttoRestart workspace rules

- Keep all OttoRestart application code and its isolated local backend in this directory. Do not edit OttoFlow files or shared repository configuration.
- This is a standalone Next.js App Router application. Its public base path is `/restart`; internal Next links should use paths relative to that base path.
- Keep account selection in the OttoRestart namespace; never read or write OttoFlow's `current_account` localStorage key.
- The API/database in `backend/` are OttoRestart-only and local-development only until authentication and deployment boundaries are reviewed. Bind the API to loopback; do not register it in the OttoFlow backend.
- Dry-Run must never persist a batch, reserve EANs, or call external services. EAN pool import is distinct from reservation.
- Do not display invented operational metrics or imply an action has run; derive counts from the isolated API.
- Do not call Aftercool or OTTO in import, validation, or pool operations.
- Do not add OTTO write calls. Restart publication remains disabled until the documented business and GTIN approvals.
- Phase 4/5 demo code lives in `features/review/`, `features/queue/`, and `app/api/demo/`. Keep it in separate in-memory browser sessions; never seed it into Phase 3's database or expose fixtures as real operational results. Demo dry-run queueing is allowed only for immutable approved demo snapshots. It must never call OTTO/Aftercool or be presented as production enqueue, submission, publication, or ONLINE confirmation.

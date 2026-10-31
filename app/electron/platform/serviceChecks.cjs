// §3 ensure-backend checks shared by every platform: the pure halves of the
// service-install failure detail and the /health identity check, kept out of
// main.cjs so the §15 tests can load them (main.cjs has no importable module
// structure).

// §3: `python -m autowright.service install` reports its reason as a result
// line on stdout and exits 1, and execFile's err.message is only "Command
// failed: <cmd>" plus stderr — so stdout comes first, and the bare exec error
// is the last resort.
function serviceFailureDetail(err, stdout, stderr) {
  return String(stdout || stderr || err?.message || '').trim()
}

// §3: a 200 on the recorded port proves nothing on its own. Only a /health
// body that names this app (`app === 'Autowright'`), carries a non-empty
// version and, when both it and backend.json carry a pid, answers with that
// same pid is ours; another Autowright backend squatting the recorded port
// (another user's, an isolated AUTOWRIGHT_HOME's) is not. A body with no pid
// (every backend released before 2026-10-30) is judged by app and version
// alone, or the first launch after an update would take the previous
// release's backend for a foreign one. Returns the version, or null when the
// answer is not ours.
function healthVersion(body, info) {
  if (body?.app !== 'Autowright') return null
  if (info?.pid != null && body?.pid != null && body.pid !== info.pid) return null
  return String(body.version ?? '') || null
}

module.exports = { serviceFailureDetail, healthVersion }

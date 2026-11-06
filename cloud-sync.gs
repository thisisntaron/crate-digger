// Crate Digger cloud sync. Paste this into your sheet's Apps Script (Extensions > Apps Script).
// 1. Change the secret word below to a long phrase only you know (a few random words works well).
// 2. Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone.
// 3. Copy the Web app URL into Crate Digger, along with the same secret word.
var TOKEN = 'change-this-to-your-own-secret-word';

var DEFAULT_TOKEN = 'change-this-to-your-own-secret-word';
var TAB = 'Crate Digger Save';   // a hidden tab this script creates; your Song list tab is never touched
var CHUNK = 40000;               // one spreadsheet cell holds at most 50000 characters

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function tab_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(TAB);
  if (!sh) {
    sh = ss.insertSheet(TAB);
    sh.hideSheet();
  }
  return sh;
}

function read_() {
  var sh = tab_();
  var last = sh.getLastRow();
  if (last < 1) return { rev: 0, t: 0, device: '', data: '' };
  var meta = {};
  try { meta = JSON.parse(sh.getRange(1, 1).getValue() || '{}'); } catch (e) {}
  var data = '';
  if (last > 1) {
    data = sh.getRange(2, 1, last - 1, 1).getValues().map(function (r) { return r[0]; }).join('');
  }
  return { rev: meta.rev || 0, t: meta.t || 0, device: meta.device || '', data: data };
}

function badToken_(t) {
  return TOKEN === DEFAULT_TOKEN || !t || t !== TOKEN;
}

function doGet(e) {
  var p = (e && e.parameter) || {};
  if (badToken_(p.token)) return out_({ ok: false, error: 'token' });
  var r = read_();
  r.ok = true;
  return out_(r);
}

function doPost(e) {
  var body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return out_({ ok: false, error: 'bad' }); }
  if (badToken_(body.token)) return out_({ ok: false, error: 'token' });
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var cur = read_();
    // Refuse a save that was based on an older version, so two devices can never silently overwrite each other.
    if (!body.force && (body.base || 0) !== cur.rev) {
      return out_({ ok: false, error: 'conflict', rev: cur.rev, t: cur.t, device: cur.device });
    }
    var t = body.t || Date.now();
    var data = String(body.data || '');
    var rows = [[JSON.stringify({ rev: cur.rev + 1, t: t, device: body.device || '' })]];
    for (var i = 0; i < data.length; i += CHUNK) rows.push([data.substr(i, CHUNK)]);
    var sh = tab_();
    sh.clear();
    var rg = sh.getRange(1, 1, rows.length, 1);
    rg.setNumberFormat('@');
    rg.setValues(rows);
    return out_({ ok: true, rev: cur.rev + 1, t: t });
  } finally {
    lock.releaseLock();
  }
}

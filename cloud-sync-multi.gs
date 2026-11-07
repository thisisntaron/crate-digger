// Crate Digger cloud sync, with separate saves for each person.
// Paste this over the old script in your sheet's Apps Script, then Deploy > Manage deployments >
// pencil icon > Version: New version > Deploy. The Web app link stays the same.
//
// Each line in USERS is:  'secret word': 'name of the hidden tab that holds that person's save'
// Keep YOUR line exactly as it is now: your current secret word, pointing at 'Crate Digger Save'.
// Give each other person their own long, different secret word and their own tab name.
// Anyone with the link plus a secret word can only reach the save that word points to.
var USERS = {
  'PUT-YOUR-CURRENT-SECRET-WORD-HERE': 'Crate Digger Save',
  'PUT-A-DIFFERENT-LONG-WORD-FOR-PERSON-2-HERE': 'Crate Digger Save - Person 2'
};

var CHUNK = 40000;               // one spreadsheet cell holds at most 50000 characters

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

// Returns the tab name for a secret word, or '' if the word is unknown or still a placeholder.
function tabFor_(token) {
  if (!token || String(token).indexOf('PUT-') === 0) return '';
  return Object.prototype.hasOwnProperty.call(USERS, token) ? USERS[token] : '';
}

function tab_(name) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.hideSheet();
  }
  return sh;
}

function read_(name) {
  var sh = tab_(name);
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

function doGet(e) {
  var p = (e && e.parameter) || {};
  var name = tabFor_(p.token);
  if (!name) return out_({ ok: false, error: 'token' });
  var r = read_(name);
  r.ok = true;
  return out_(r);
}

function doPost(e) {
  var body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return out_({ ok: false, error: 'bad' }); }
  var name = tabFor_(body.token);
  if (!name) return out_({ ok: false, error: 'token' });
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var cur = read_(name);
    // Refuse a save that was based on an older version, so two devices can never silently overwrite each other.
    if (!body.force && (body.base || 0) !== cur.rev) {
      return out_({ ok: false, error: 'conflict', rev: cur.rev, t: cur.t, device: cur.device });
    }
    var t = body.t || Date.now();
    var data = String(body.data || '');
    var rows = [[JSON.stringify({ rev: cur.rev + 1, t: t, device: body.device || '' })]];
    for (var i = 0; i < data.length; i += CHUNK) rows.push([data.substr(i, CHUNK)]);
    var sh = tab_(name);
    sh.clear();
    var rg = sh.getRange(1, 1, rows.length, 1);
    rg.setNumberFormat('@');
    rg.setValues(rows);
    return out_({ ok: true, rev: cur.rev + 1, t: t });
  } finally {
    lock.releaseLock();
  }
}

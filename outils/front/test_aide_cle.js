/* La fiche doit dire ce qu'on attend dans le champ de la clé. */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const cible = process.argv[2] || path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable',
  url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
const ERREURS = []; w.addEventListener('error', (e) => ERREURS.push(String(e.message)));
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
let DETECTE = null;
w.fetch = (u) => {
  if (String(u).includes('/api/detect') && DETECTE) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(DETECTE) });
  }
  return Promise.reject(new Error('hors ligne'));
};
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));

setTimeout(async () => {
  inj('apps = []; CONN = "ok"; i18n.langue = "fr"; i18n.appliquer(document);'
    + 'CAPA = { npm: { format: "couple" }, plex: { format: "cle" },'
    + '         glances: { format: "couple" }, netdata: { format: "aucun" },'
    + '         proxmox: { format: "jeton" }, vaultwarden: { format: "admin" },'
    + '         jackett: { format: "cle" } };'
    + 'openAppModal(null);');
  await attends(150);

  console.log('=== ce que la fiche dit, selon le service ===');
  for (const k of ['plex', 'npm', 'netdata', 'proxmox', 'vaultwarden', 'jackett', 'glances']) {
    inj('$("m-type").value = "' + k + '"; syncKind();');
    await attends(30);
    console.log('    ' + k.padEnd(12)
      + ($('m-key-aide').hidden ? '(rien)' : $('m-key-aide').textContent).slice(0, 96));
    console.log('    ' + ' '.repeat(12) + 'champ : « ' + $('m-key').placeholder + ' »');
  }

  console.log('\n=== la détection renseigne la forme sans attendre la table ===');
  inj('CAPA = null;');
  DETECTE = { trouve: true, type: 'omv', nom: 'OpenMediaVault', cle_libelle: 'Identifiants',
              cle_requise: true, cle_format: 'couple', donnees: [] };
  $('m-url').value = 'http://192.168.0.9';
  inj('detecterEtTester();');
  await attends(250);
  console.log('    libellé : ' + $('m-key-label').textContent);
  console.log('    note    : ' + $('m-key-aide').textContent.slice(0, 80));
  console.log('    champ   : « ' + $('m-key').placeholder + ' »');

  console.log('\n=== un service inconnu ne raconte rien ===');
  inj('$("m-type").value = ""; syncKind();');
  await attends(30);
  console.log('    note masquée : ' + $('m-key-aide').hidden);

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);

/* L'écran des notifications : ce qu'il montre, et ce qu'il enregistre. */
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
const ENVOIS = [];
let NOTIF_SRV = { actif: true, url: 'https://ntfy.sh/maison', hors_ligne: true,
                  seuil: true, retour: false, dernier: Date.now() / 1000 - 3600,
                  erreur: '', sourdine: ['UniFi'] };
w.fetch = (u, o) => {
  const url = String(u);
  ENVOIS.push({ url, corps: (o && o.body) || '' });
  if (url.includes('/api/securite')) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({
      points: [], score: 7, total: 9, mdp: true, profils: 1, sans_mdp: [],
      limiteur: { essais: 5, fenetre: 300 }, echecs: [], iterations: 600000,
      acces: { ip: '192.168.0.42', local: true, relaye: false, portail: '' },
      notif: NOTIF_SRV }) });
  }
  if (url.includes('/api/notif/essai')) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, motif: '200' }) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
};
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const clic = (el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const cocher = (id, v) => {
  $(id).checked = v;
  $(id).dispatchEvent(new w.Event('change', { bubbles: true }));
};

setTimeout(async () => {
  inj('apps = []; CONN = "ok"; i18n.langue = "fr"; i18n.appliquer(document);'
    + 'openSettings(); spNav("notif");');
  await attends(300);

  console.log('=== ce que la page montre ===');
  console.log('    adresse       : ' + $('nt-url').value);
  console.log('    envoi actif   : ' + $('nt-actif').checked);
  console.log('    cases         : hors ligne=' + $('nt-hors').checked
    + ' seuil=' + $('nt-seuil').checked + ' retour=' + $('nt-retour').checked);
  console.log('    état          : « ' + $('nt-etat').textContent + ' »');
  console.log('    pastille      : ' + $('nt-puce').className);
  console.log('    menu          : « ' + $('nq-notif').textContent + ' »');

  console.log('\n=== cocher « un service revient » enregistre ===');
  const avant = ENVOIS.length;
  cocher('nt-retour', true);
  await attends(150);
  const push = ENVOIS.slice(avant).find((e) => e.url.includes('/cfg'));
  console.log('    config poussée : ' + (push ? 'oui' : '!!! non'));
  if (push) {
    const n = JSON.parse(push.corps).notif;
    console.log('    envoyé au serveur : ' + JSON.stringify(n));
  }

  console.log('\n=== l\'essai passe par le serveur ===');
  clic($('nt-test'));
  await attends(200);
  const essai = ENVOIS.filter((e) => e.url.includes('/api/notif/essai')).pop();
  console.log('    appel  : ' + (essai ? essai.url : '!!! aucun'));
  console.log('    corps  : ' + (essai ? essai.corps : ''));
  console.log('    toast  : ' + (($('toast') || {}).textContent || '(aucun)'));

  console.log('\n=== une adresse absente se dit ===');
  NOTIF_SRV = { actif: false, url: '', hors_ligne: true, seuil: true, retour: false,
                dernier: 0, erreur: '', sourdine: [] };
  inj('chargerNotif();');
  await attends(200);
  console.log('    état   : « ' + $('nt-etat').textContent + ' »');
  console.log('    menu   : « ' + $('nq-notif').textContent + ' »');

  console.log('\n=== un envoi en échec se dit aussi ===');
  NOTIF_SRV = { actif: true, url: 'https://ntfy.sh/x', hors_ligne: true, seuil: true,
                retour: false, dernier: 0, erreur: 'HTTP 403', sourdine: [] };
  inj('chargerNotif();');
  await attends(200);
  console.log('    état   : « ' + $('nt-etat').textContent + ' »');
  console.log('    pastille : ' + $('nt-puce').className);

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);

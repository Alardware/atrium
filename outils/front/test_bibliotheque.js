/* Choisir un logo dans la bibliothèque, sans quitter la fiche. */
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

const APPELS = [];
let REPONSE = { icones: ['deluge', 'deluge-light'], base: 'https://cdn.exemple/svg/', catalogue: 2253 };
let PANNE = false;
w.fetch = (u) => {
  APPELS.push(String(u));
  if (String(u).includes('/api/icones')) {
    if (PANNE) return Promise.resolve({ ok: false, status: 502, json: () => Promise.resolve({}) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve(REPONSE) });
  }
  return Promise.reject(new Error('hors ligne'));
};
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const saisir = (id, v) => {
  $(id).value = v;
  $(id).dispatchEvent(new w.Event('input', { bubbles: true }));
};
const clic = (el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));

setTimeout(async () => {
  inj('apps = []; CONN = "ok"; i18n.langue = "fr"; openAppModal(null);');
  await attends(150);

  console.log('=== une lettre ne déclenche rien ===');
  saisir('m-ico-q', 'd');
  await attends(400);
  console.log('    appels au serveur : ' + APPELS.filter((a) => a.includes('/api/icones')).length
    + '   résultats affichés : ' + !$('m-ico-res').hidden);

  console.log('\n=== « deluge » propose ses vignettes ===');
  saisir('m-ico-q', 'deluge');
  await attends(500);
  const vignettes = [...w.document.querySelectorAll('.ico-choix')];
  console.log('    vignettes : ' + vignettes.length
    + '   sources : ' + vignettes.map((b) => b.querySelector('img').src.split('/').pop()).join(', '));

  console.log('\n=== un clic renseigne l\'URL du logo ===');
  clic(vignettes[1]);
  await attends(80);
  console.log('    champ URL : ' + $('m-logo').value);
  console.log('    vignette marquée : ' + vignettes[1].classList.contains('on')
    + '   l\'autre : ' + vignettes[0].classList.contains('on'));

  console.log('\n=== la frappe ne réinterroge pas le serveur à chaque lettre ===');
  const avant = APPELS.filter((a) => a.includes('/api/icones')).length;
  saisir('m-ico-q', 'delu');
  saisir('m-ico-q', 'delug');
  saisir('m-ico-q', 'deluge');
  await attends(500);
  console.log('    appels ajoutés : ' + (APPELS.filter((a) => a.includes('/api/icones')).length - avant)
    + ' (attendu 1)');

  console.log('\n=== rien trouvé : on le dit, et on n\'insiste pas ===');
  REPONSE = { icones: [], base: 'https://cdn.exemple/svg/', catalogue: 2253 };
  saisir('m-ico-q', 'zzzz');
  await attends(400);
  console.log('    message : ' + $('m-ico-res').textContent.trim().slice(0, 60));
  const apres = APPELS.filter((a) => a.includes('/api/icones')).length;
  saisir('m-ico-q', 'zzzzz');
  await attends(400);
  console.log('    appel pour « zzzzz » : '
    + (APPELS.filter((a) => a.includes('/api/icones')).length - apres) + ' (attendu 0)');

  console.log('\n=== bibliothèque injoignable ===');
  PANNE = true;
  saisir('m-ico-q', 'plex');
  await attends(400);
  console.log('    message : ' + $('m-ico-res').textContent.trim().slice(0, 60));

  console.log('\n=== réouvrir une fiche repart de zéro ===');
  inj('closeAppModal(); openAppModal(null);');
  await attends(150);
  console.log('    champ vide : ' + ($('m-ico-q').value === '')
    + '   résultats masqués : ' + $('m-ico-res').hidden);

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);

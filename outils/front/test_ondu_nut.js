/* La carte Onduleur, nourrie par un NUT branché en direct (sans Home Assistant). */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const cible = path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable',
  url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
const ERREURS = []; w.addEventListener('error', (e) => ERREURS.push(String(e.message)));
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
w.fetch = () => Promise.reject(new Error('hors ligne'));
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const carte = () => w.document.querySelector('#featured [data-w="onduleur"]');
const lire = () => {
  const c = carte();
  if (!c) return '(aucune carte)';
  const g = c.querySelector('.cock-big');
  const lignes = [...c.querySelectorAll('.cock-row, .c-row')]
    .map((r) => r.textContent.replace(/\s+/g, ' ').trim());
  return (g ? g.textContent.replace(/\s+/g, ' ').trim() : '') + ' | ' + lignes.join('  ·  ');
};
const app = { id: 'onduleur-cave', nom: 'Onduleur', role: 'Eaton 5E', cat: 'Serveur',
  url: 'http://192.168.1.10:3493', logoUrl: '', token: '', apiKey: '', tempEntity: '',
  type: 'nut', fav: false, masque: [] };
const mes = (l) => l.map(([id, lab, val, num]) => ({ id, lab, val, num }));

setTimeout(async () => {
  inj(`apps = ${JSON.stringify([app])}; CONN = 'error'; STATES = [];
    STATS = { 'onduleur-cave': ${JSON.stringify(mes([
      ['batterie','BATTERIE','100 %',100], ['autonomie','AUTONOMIE','1 h 12',null],
      ['alim','ALIMENTATION','sur secteur',null], ['charge_ups','CHARGE','23 %',23],
      ['tension','TENSION','233 V',null], ['puissance','PUISSANCE','110 W',null]]))} };
    WIDGETS = ['onduleur']; EDIT_W = false; i18n.langue = 'fr';
    i18n.appliquer(document); appliquerDisposition();`);
  await attends(250);
  console.log('=== onduleur lu en direct, sans Home Assistant ===');
  console.log('    connexion HA : ' + w.eval('CONN'));
  console.log('    ' + lire());
  console.log('    carte en alerte : ' + (carte() && carte().classList.contains('w-alerte')));

  console.log('\n=== coupure de courant, batterie à 12 % ===');
  inj(`STATS['onduleur-cave'] = ${JSON.stringify(mes([
    ['batterie','BATTERIE','12 %',12], ['autonomie','AUTONOMIE','4 min',null],
    ['alim','ALIMENTATION','sur batterie',null], ['charge_ups','CHARGE','61 %',61],
    ['puissance','PUISSANCE','290 W',null]]))}; appliquerDisposition();`);
  await attends(200);
  console.log('    ' + lire());
  console.log('    carte en alerte : ' + (carte() && carte().classList.contains('w-alerte')));

  console.log('\n=== sans mesure, la carte ne se propose pas ===');
  inj(`STATS = {}; window.__d = REGISTRE_W.onduleur.dispo(); appliquerDisposition();`);
  await attends(200);
  console.log('    proposée : ' + w.__d + '   carte présente : ' + !!carte());
  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);

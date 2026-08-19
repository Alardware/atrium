/* Le cadre de la carte « En ce moment », quand la source ne donne pas d'image.

   Une chaîne de télévision ou une vidéo YouTube lue sur un téléviseur arrive
   sans « entity_picture » : le cadre restait vide, avec le mot « affiche », ce
   qui se lit comme une image qui n'a pas chargé.
*/
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
w.fetch = () => Promise.reject(new Error('hors ligne'));
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const dit = (bon, titre, detail) =>
  console.log((bon ? '  ok    ' : '  ECHEC ') + titre + (detail ? '   ' + detail : ''));

const image = () => $('np-poster').querySelector('img');

setTimeout(async () => {
  inj(`apps = []; CONN = 'ok'; i18n.langue = 'fr'; i18n.appliquer(document);
    MEDIA = { appLabel: 'YouTube', etat: 'playing', titre: 'Une vidéo',
              ligne2: ['Séjour'], poster: '', type: 'video' };
    MEDIAS = [MEDIA]; renderLive();`);
  await attends(200);

  console.log('=== YouTube sans affiche ===');
  dit(!!image(), 'le cadre reçoit une image');
  dit((image() || {}).src.includes('youtube'), 'et c\'est le logo de la source',
      ((image() || {}).src || '').split('/').pop());
  dit($('np-poster').classList.contains('marque'),
      'le cadre se sait « logo », pas « affiche »');

  console.log('\n=== une vraie affiche reprend sa place ===');
  inj(`MEDIA.poster = 'http://nas.local/affiche.jpg'; renderLive();`);
  await attends(150);
  dit((image() || {}).src.includes('affiche.jpg'), 'l\'affiche prime sur le logo',
      ((image() || {}).src || '').split('/').pop());
  dit(!$('np-poster').classList.contains('marque'), 'et le cadre la montre en entier');

  console.log('\n=== une source inconnue au catalogue ===');
  inj(`MEDIA.poster = ''; MEDIA.appLabel = 'Machin TV'; renderLive();`);
  await attends(150);
  const img = image();
  if (img) { img.onerror(); }
  dit(!image(), 'le dessin manquant est retiré');
  dit(!$('np-poster').classList.contains('marque'),
      'et le mot « affiche » revient', $('np-poster').textContent.trim());

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);

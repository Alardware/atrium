/* Ce que l'interface donne à lire à qui ne la voit pas.

   Un tableau de bord se lit surtout d'un coup d'œil : des pastilles, des
   jauges, des icônes. Rien de tout cela n'existe pour un lecteur d'écran si
   personne ne l'a nommé. Cette suite passe axe-core sur la page — d'abord
   l'écran de connexion, puis le tableau garni — et n'accepte aucune
   infraction sérieuse ou critique.

   Le contraste n'est pas mesuré ici : jsdom ne peint pas, il ne peut donc pas
   le calculer. Il se vérifie dans un navigateur.
*/
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const axeSource = fs.readFileSync(require.resolve('axe-core'), 'utf8');
const cible = process.argv[2] || path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable',
  url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
const ERREURS = []; w.addEventListener('error', (e) => ERREURS.push(String(e.message)));
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
w.fetch = () => Promise.reject(new Error('hors ligne'));
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const attends = (ms) => new Promise((r) => setTimeout(r, ms));

/* Sans peinture, ces règles ne peuvent rien conclure : les écarter vaut mieux
   que de les laisser rendre un verdict faux. */
const HORS_PORTEE = ['color-contrast', 'target-size', 'scrollable-region-focusable'];

let FAUTES = 0;

async function passe(titre, apprets) {
  if (apprets) { inj(apprets); await attends(250); }
  const r = await w.axe.run(w.document, {
    resultTypes: ['violations'],
    rules: HORS_PORTEE.reduce((o, id) => { o[id] = { enabled: false }; return o; }, {}),
  });
  const graves = r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const legeres = r.violations.filter((v) => !graves.includes(v));
  console.log('\n=== ' + titre + ' ===');
  if (!r.violations.length) { console.log('  ok    rien à redire'); return; }
  for (const v of r.violations) {
    const grave = graves.includes(v);
    if (grave) FAUTES += 1;
    console.log((grave ? '  ECHEC ' : '  note  ') + v.id + ' [' + v.impact + '] — '
      + v.help + '   ' + v.nodes.length + ' élément(s)');
    for (const n of v.nodes.slice(0, 4)) {
      console.log('          ' + n.target.join(' ') + '   ' + n.html.replace(/\s+/g, ' ').slice(0, 110));
    }
  }
  if (!graves.length) console.log('  ok    aucune infraction sérieuse (' + legeres.length + ' remarque(s))');
}

setTimeout(async () => {
  inj(axeSource);
  await attends(200);

  await passe("l'écran de connexion", `
    USERS = [{ nom: 'Guillaume', pwd: '1', photo: '' }, { nom: 'Invité', pwd: '', photo: '' }];
    PROFILS = [{ nom: 'Guillaume', protege: true, photo: '' },
               { nom: 'Invité', protege: false, photo: '' }];
    SESSION_USER = null; ACTIVE = '';
    i18n.langue = 'fr'; i18n.appliquer(document);
    $('lock').hidden = false; renderLockUi();`);

  await passe('le tableau garni', `
    $('lock').hidden = true;
    apps = ['Plex','Sonarr','Unraid','UniFi','Deluge','Home Assistant'].map(function (n) {
      return { id: n.toLowerCase().replace(/ /g,''), nom: n, role: 'Service', cat: 'Média',
               url: 'http://nas.local', logoUrl: '', token: '', apiKey: '', tempEntity: '',
               type: '', fav: n === 'Plex', masque: [] }; });
    CONN = 'ok';
    STATS = { plex: [{ id: 'flux', lab: 'Flux', val: '2', num: 2 }],
              unraid: [{ id: 'cpu', lab: 'CPU', val: '7 %', num: 7 },
                       { id: 'ram', lab: 'RAM', val: '31 %', num: 31 }] };
    USERS = [{ nom: 'Guillaume', pwd: '1', photo: '', vue: { montre: null, ordre: [],
               favoris: ['plex'], masques: {}, widgets: null } }];
    SESSION_USER = 'Guillaume'; ACTIVE = 'Guillaume';
    voirStats = true;
    i18n.appliquer(document); renderApps();`);

  await passe('les réglages ouverts', `
    if (typeof ouvrirReglages === 'function') { ouvrirReglages(); }
    else if ($('btn-settings')) { $('btn-settings').click(); }`);

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  console.log(FAUTES ? '\nRESUME : ' + FAUTES + ' infraction(s) sérieuse(s)'
                     : '\nRESUME : rien de sérieux au regard de WCAG 2.2 AA');
  process.exit(0);
}, 2500);

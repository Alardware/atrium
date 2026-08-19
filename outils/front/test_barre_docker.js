/* La barre d'attente Docker, vue de la page : elle ne doit montrer que ce que
   le serveur dit avoir mesuré, et rendre la main dès que c'est fini. */
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
const attends = (ms) => new Promise((r) => setTimeout(r, ms));

/* Le serveur, tel qu'il répondrait : une liste demandée, puis dix conteneurs
   mesurés par paquets de huit. */
const SUITE = [
  { phase: 'liste', fait: 0, total: 0, encours: true },
  { phase: 'mesure', fait: 0, total: 10, encours: true },
  { phase: 'mesure', fait: 8, total: 10, encours: true },
  { phase: 'mesure', fait: 10, total: 10, encours: true },
  { phase: 'fini', fait: 10, total: 10, encours: false },
];

setTimeout(async () => {
  inj(`
    apps = []; CONN = 'ok'; i18n.langue = 'fr'; i18n.appliquer(document);
    DOCKER.actif = true;
    DOCKER.noms = [1,2,3,4,5,6,7,8,9,10].map(function (i) {
      return { nom: 'app-' + i, etat: 'running' }; });
    window.__srv = 0; window.__av = 0; window.__suite = ${JSON.stringify(SUITE)};
    /* On répond à la place du serveur, en avançant d'un cran à chaque demande. */
    relayFetch = function (chemin) {
      if (chemin.indexOf('/api/conteneurs/avancement') === 0) {
        var e = window.__suite[Math.min(window.__av, window.__suite.length - 1)];
        window.__av += 1;
        return Promise.resolve({ ok: true, json: function () { return Promise.resolve(e); } });
      }
      if (chemin.indexOf('/api/serveur') === 0) {
        window.__srv += 1;
        return Promise.reject(new Error('hors ligne'));
      }
      return Promise.reject(new Error('hors ligne'));
    };
    ouvrirServeurs();
    clearInterval(SRV_TIMER);
  `);
  /* La page s'ouvre en allant chercher le serveur : on la laisse finir son
     aller-retour raté avant de poser le décor, sinon elle repeint par-dessus. */
  await attends(300);
  inj(`peindreServeurs({ hote: { disponible: true, nom: 'Test' }, docker: true,
                         conteneurs: [], conteneurs_a: 0,
                         historique: { cpu: [], memoire: [] } });
       window.__srv = 0;`);
  await attends(120);

  const barre = () => w.document.querySelector('.dk-progress');
  const jus = () => w.document.querySelector('.dk-progress > i');
  const pas = () => w.document.querySelector('.dk-step');
  const lire = () => ({
    largeur: jus().style.width || '(aucune)',
    balaye: barre().classList.contains('indet'),
    texte: pas().textContent,
  });

  console.log('=== le squelette est là, avant toute nouvelle du serveur ===');
  console.log('    ' + JSON.stringify(lire()));

  const ATTENDU = [
    ['liste demandee', '(aucune)', true],
    ['total connu, rien de mesure', '(aucune)', true],
    ['8 sur 10', '80%', false],
    ['10 sur 10', '100%', false],
  ];
  for (let i = 0; i < ATTENDU.length; i++) {
    await attends(400);
    const e = lire();
    const [titre, largeur, balaye] = ATTENDU[i];
    const bon = e.largeur === largeur && e.balaye === balaye;
    console.log((bon ? '  ok    ' : '  ECHEC ') + titre
      + ' -> largeur ' + e.largeur + (e.balaye ? ' (balayage)' : '')
      + ' | ' + e.texte
      + (bon ? '' : '   attendu ' + largeur + (balaye ? ' + balayage' : '')));
  }

  console.log('\n=== fini : la page va chercher la vraie table sans attendre ===');
  const avant = w.__srv;
  await attends(700);
  const parti = !barre();
  console.log((w.__srv > avant ? '  ok    ' : '  ECHEC ')
    + 'appels a /api/serveur apres le dernier conteneur : ' + (w.__srv - avant));
  console.log((parti ? '  ok    ' : '  ECHEC ') + 'le squelette a cede la place : ' + parti);

  console.log('\n=== le compteur n est plus interroge une fois le squelette parti ===');
  const demandes = w.__av;
  await attends(900);
  console.log(((w.__av === demandes) ? '  ok    ' : '  ECHEC ')
    + 'demandes supplementaires : ' + (w.__av - demandes));

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);

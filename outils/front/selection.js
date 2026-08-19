/* Sélectionner le texte d'un champ ne doit pas fermer la fenêtre.

   Le geste reproduit est celui de la capture : on appuie dans le champ
   « Libellé », on tire vers la gauche au-delà du cadre, on relâche sur le fond
   sombre. Le navigateur envoie alors un « click » dont la cible est l'ancêtre
   commun des deux points — le fond — et l'ancien code y voyait un clic dehors.

   On vérifie aussi que le fond ferme toujours quand on le clique vraiment,
   sans quoi le remède emporterait la fonction. */
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

const envoyer = (el, type) => el.dispatchEvent(
  new w.MouseEvent(type, { bubbles: true, cancelable: true }));

/* Le geste complet, tel que le navigateur le raconte : appui sur « depart »,
   relâchement sur « arrivee », puis clic sur leur ancêtre commun. */
function glisser(depart, arrivee, ancetre) {
  envoyer(depart, 'pointerdown');
  envoyer(depart, 'mousedown');
  envoyer(arrivee, 'pointerup');
  envoyer(arrivee, 'mouseup');
  envoyer(ancetre, 'click');
}

setTimeout(async () => {
  inj('apps = [{ nom: "Unraid", role: "Monitoring Unraid", cat: "Serveur",'
    + ' url: "http://nas.lan", logoUrl: "", token: "", apiKey: "k", tempEntity: "",'
    + ' type: "unraid", fav: false, masque: [] }];'
    + 'CONN = "ok"; STATS = {}; SUPER = { etats: {}, alertes: { total: 0, non_lues: 0,'
    + ' problemes: 0, niveau: "", alertes: [] } }; renderApps();');
  await attends(200);

  const essais = [
    ['fiche d\'application', 'ov-app', () => inj('openAppModal(0);'), 'm-role'],
    ['confirmation', 'ov-confirm', () => inj('openConfirm("t", "s", "ok", function () {});'), 'c-title'],
  ];

  for (const [nom, id, ouvrir, champ] of essais) {
    console.log('=== ' + nom + ' ===');
    ouvrir();
    await attends(80);
    console.log('    ouverte                       : ' + !$(id).hidden);

    glisser($(champ), $(id), $(id));
    await attends(40);
    console.log('    sélection qui déborde du champ: '
      + ($(id).hidden ? '!!! FERMÉE' : 'reste ouverte'));

    if (champ === 'm-role') {
      console.log('    texte conservé                : '
        + JSON.stringify($('m-role').value));
    }

    /* Vrai clic sur le fond : appui et relâchement au même endroit. */
    glisser($(id), $(id), $(id));
    await attends(40);
    console.log('    clic franc sur le fond        : '
      + ($(id).hidden ? 'ferme' : '!!! NE FERME PLUS'));
    $(id).hidden = true;
  }

  console.log('\n=== geste inverse : commencé sur le fond, fini dans la fenêtre ===');
  inj('openAppModal(0);');
  await attends(80);
  glisser($('ov-app'), $('m-role'), $('ov-app'));
  await attends(40);
  console.log('    fenêtre : ' + ($('ov-app').hidden ? '!!! FERMÉE' : 'reste ouverte'));
  $('ov-app').hidden = true;

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);

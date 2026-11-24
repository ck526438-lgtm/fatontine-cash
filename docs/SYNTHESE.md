# Synthèse : FATONTINE CASH, du prototype à la V1 en production

**Méthode :** analyse documentaire (cahier des charges V1.0) et audit technique du prototype HTML « Liquid Glass »
**Participants :** 1 porteuse de projet (Fatima), à travers son cahier des charges. Aucun entretien direct.
**Date :** 24 novembre 2026 | **Rédaction :** équipe produit FATONTINE CASH

---

### Résumé

Le cahier des charges décrit un outil de **confiance** plus qu'un outil de comptabilité : on vérifie l'identité avant l'adhésion, on n'accepte aucun paiement sur simple déclaration, et on justifie chaque changement d'ordre de passage. Le prototype reprend bien l'identité visuelle (bleu nuit, or, blanc) et les parcours, mais il ne peut pas recevoir de vrais membres. Son JavaScript est cassé à trois endroits, l'authentification accepte n'importe quel mot de passe de 4 caractères, et toutes les données vivent dans le `localStorage` d'un seul navigateur. La V1 reconstruit donc le produit sur Django + PostgreSQL, comme prévu au §9, en gardant le design glass à l'identique.

---

### Thèmes clés

#### Thème 1 : La confiance passe par la preuve
**Fréquence :** 6 sections sur 14 (§2, §5.2, §5.7, §5.8, §8, §13)
**Résumé :** chaque étape sensible doit laisser une trace vérifiable : la pièce d'identité, le témoin, l'ID Wave, la justification d'un changement de tour et l'historique administratif.
**Éléments d'appui :**
- « Le site ne devra pas considérer un paiement comme confirmé uniquement sur la base d'une déclaration du membre. » (§5.8)
- « Les modifications de l'ordre de passage devront être enregistrées et justifiées. » (§5.7)
- « Aucun membre ne pourra être ajouté définitivement à un groupe avant validation de son dossier. » (§5.2)

**Implication :** l'ID Wave est obligatoire et **unique** (un même ID ne peut pas servir deux fois). Rien n'est jamais supprimé : une correction annule l'opération avec un motif, et un journal d'audit horodaté enregistre chaque action.

#### Thème 2 : Les calculs doivent pouvoir être vérifiés
**Fréquence :** §5.6, §6, §7, §10, §12 et le « point financier important » du §13
**Résumé :** Fatima doit pouvoir expliquer chaque franc : ce qui est attendu, ce qui a été versé, l'écart, et ce qui a été distribué.
**Éléments d'appui :**
- « La consultation des soldes et écarts. » (§5.6)
- « Les montants annoncés sur l'affiche correspondent à 90 % de cette collecte. Cette différence devra être expliquée et approuvée par Fatima. » (§13)

**Implication :** le système calcule pour chaque membre l'attendu (cotisation × jours écoulés), le versé et l'écart. Le calcul a aussi révélé que **l'hypothèse des 90 % est fausse** : 550 × 10 × 10 = 55 000 F pour 50 000 F reçus, soit 10/11 (≈ 90,91 %). Les frais de gestion valent 1/11 de la collecte, c'est-à-dire 10 % du montant **reçu**, et non de la collecte. La répartition est donc calculée à partir de chaque formule, sans taux codé en dur.

#### Thème 3 : Une seule administratrice, souvent sur mobile
**Fréquence :** §1, §3, §4, §10
**Résumé :** Fatima gère tout seule, depuis un ordinateur, une tablette ou un téléphone. Chaque écran doit permettre une action rapide.
**Éléments d'appui :**
- « La plateforme sera accessible depuis un ordinateur, une tablette ou un téléphone. » (§1)
- « Le site devra… être rapide et facile à utiliser. » (§10)

**Implication :** la navigation passe par un menu latéral repliable, des actions rapides sur le tableau de bord et l'encaissement en un clic depuis la liste des retards.

#### Thème 4 : WhatsApp sert de canal de communication
**Fréquence :** §5.9, §5.10, §11
**Résumé :** les reçus sont partagés sur WhatsApp, et les notifications automatisées WhatsApp sont prévues pour plus tard.
**Implication :** en V1, chaque notification (rappel, retard, validation, refus, prochaine distribution) produit un message prêt à envoyer via un lien `wa.me`. L'envoi automatique pourra venir ensuite sans changer le modèle de données.

#### Thème 5 : Les données personnelles sont sensibles
**Fréquence :** §5.2, §8
**Résumé :** les CNI et les photos des membres sont des données à caractère personnel, protégées par la loi ivoirienne n° 2013-450.
**Implication :** les pièces sont stockées hors du dossier public et ne sont servies qu'à l'administratrice connectée. Le consentement est horodaté, et une commande de purge applique une durée de conservation configurable.

---

### Audit du prototype : constats

| # | Constat (observation) | Gravité |
|---|---|---|
| 1 | Lignes 1082-1098 : un ancien `DEFAULT_STATE` collé après `resetDemoData()` provoque une erreur de syntaxe, et **aucun script ne s'exécute** | Bloquant |
| 2 | Lignes 1716 et 1834 : le début des gabarits `<tr>` manque dans le journal des cotisations et la table des bénéficiaires | Bloquant |
| 3 | `appState`, `loadState`, `saveState` et `resetDemoData` sont déclarés deux fois (`let` en double, donc erreur) | Bloquant |
| 4 | Connexion acceptée avec l'e-mail en clair et **n'importe quel mot de passe ≥ 4 caractères**, et le mot de passe est pré-rempli dans le HTML | Critique |
| 5 | Données stockées dans le `localStorage` : aucune sauvegarde, rien de partagé entre appareils, effaçables par le navigateur | Critique |
| 6 | `approveInscription` plante s'il n'existe aucun groupe (`targetGroup.id` vaut undefined), ne vérifie pas la limite de 10 places et fixe la date de tour en dur au `2026-12-25` | Majeur |
| 7 | Les retards ne comptent que les paiements « d'aujourd'hui ». Pas de notion d'attendu ni d'écart cumulé | Majeur |
| 8 | Modifier un tour peut donner deux membres au même rang, et les dates ne sont pas recalculées | Majeur |
| 9 | Le fichier joint (CNI, photo) n'est jamais enregistré | Majeur |
| 10 | Le texte saisi est injecté par `innerHTML`, sans échappement (risque XSS) | Majeur |
| 11 | La jauge affiche toujours « 90 % », quelle que soit la progression réelle | Mineur |
| 12 | La marge de « 10 % » est appliquée à la collecte, alors que la grille de l'affiche donne 1/11 de la collecte (≈ 9,09 %) : 500 F d'écart par tour de 55 000 F sur la formule F1, 30 000 F sur F8 | Majeur (financier) |

---

### Constats → opportunités

| Constat | Opportunité (V1) | Impact | Effort |
|---|---|---|---|
| Le prototype ne s'exécute pas et ne protège rien | Backend Django : vrais comptes, sessions, CSRF, échappement automatique | Élevé | Élevé |
| Un paiement doit être prouvé | ID Wave obligatoire et unique, référence `OP-` unique, annulation motivée au lieu de suppression | Élevé | Faible |
| Les écarts doivent être visibles | Attendu, versé et écart calculés par membre, avec un rapport des retards | Élevé | Moyen |
| La répartition 90/10 annoncée ne correspond pas à la grille (10/11) | Répartition calculée formule par formule, et frais affichés par tour | Élevé | Faible |
| L'ordre de passage doit être juste | Échange de rangs (jamais de doublon), recalcul des dates, justification obligatoire | Moyen | Moyen |
| Tout passe par WhatsApp | Notifications et reçus prêts à envoyer (`wa.me`) | Moyen | Faible |
| Les pièces d'identité sont des données sensibles | Stockage privé, accès réservé à l'administratrice, purge selon une durée de conservation | Élevé | Moyen |
| Des rapports sont exigés | Rapports jour/semaine/mois, par groupe, individuels, retards et distributions, en PDF et Excel | Élevé | Moyen |

---

### Segments identifiés

| Segment | Caractéristiques | Besoins | Part estimée |
|---|---|---|---|
| Administratrice (Fatima) | Seule utilisatrice connectée, souvent sur mobile | Vitesse, preuves, vue d'ensemble | 1 compte |
| Membres « petites formules » (550 à 2 200 FCFA) | Versements quotidiens, souvent en espèces | Reçu immédiat, rappel quotidien | Majorité probable |
| Membres « grandes formules » (≥ 11 000 FCFA) | Montants élevés, surtout via Wave | Traçabilité Wave, date de réception certaine | Minorité |
| Témoins | Ne sont pas utilisateurs, mais garants | Être joignables | 1 par membre |

---

### Recommandations

1. **Priorité haute : mettre la V1 Django en production avant d'inscrire le premier membre.** Les constats 1 à 5 rendent le prototype inutilisable avec de vraies données.
2. **Priorité haute : faire valider par Fatima les points du §13**, en premier lieu les frais de gestion. Elle doit confirmer qu'ils valent bien 1/11 de la collecte (10 % de la réception), comme le dit l'affiche, et non 10 % de la collecte. Viennent ensuite les règles de retard, de remplacement et d'abandon, avant le premier cycle réel.
3. **Priorité moyenne : définir la durée de conservation des pièces** (par défaut, 24 mois après la clôture) et la faire figurer dans le texte de consentement.
4. **Priorité plus faible : automatiser les notifications WhatsApp** (évolution §11), une fois le volume de membres connu.

### Questions à approfondir
- Un membre en retard garde-t-il son tour ? Y a-t-il des pénalités ?
- Que se passe-t-il si un membre abandonne après avoir reçu sa part ?
- Comment Wave est-il vérifié : capture d'écran, relevé marchand, ou API plus tard ?
- La marge de 10 % couvre-t-elle des frais (déplacements, Wave) qui devraient apparaître comme « dépenses autorisées » ?

### Notes de méthode
Cette synthèse ne repose sur **aucun entretien utilisateur**. Elle s'appuie sur le cahier des charges écrit et sur la lecture du code du prototype. Les « fréquences » comptent des sections du document, pas des personnes. Il faudra les confirmer par un entretien avec Fatima et par l'observation des premiers cycles réels.

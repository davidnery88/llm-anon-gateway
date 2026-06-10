#!/usr/bin/env python3
"""Crée et remplit demo/demo.sqlite avec un schéma réaliste FR+DE.

Tables :
  clients               — sociétaires (PII : nom, AVS, email, tél, adresse)
  vehicules             — véhicules assurés (plaque CH, VIN, propriétaire)
  contrats_assurance    — polices auto / ménage / voyage (IBAN, prime)
  sinistres             — déclarations (description texte libre avec PII)
  interventions_assistance — dépannage routier / médical à l'étranger
  agents                — collaborateurs (PII employés internes)

Lance : python demo/seed_db.py
Idempotent — DROP puis CREATE à chaque exécution.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "demo.sqlite"

SCHEMA = """
DROP TABLE IF EXISTS sinistres;
DROP TABLE IF EXISTS interventions_assistance;
DROP TABLE IF EXISTS contrats_assurance;
DROP TABLE IF EXISTS vehicules;
DROP TABLE IF EXISTS clients;
DROP TABLE IF EXISTS agents;

CREATE TABLE clients (
    client_id     INTEGER PRIMARY KEY,
    nom           TEXT NOT NULL,
    prenom        TEXT NOT NULL,
    no_avs        TEXT NOT NULL,             -- 756.XXXX.XXXX.XX
    email         TEXT NOT NULL,
    telephone     TEXT NOT NULL,             -- +41 XX XXX XX XX
    adresse       TEXT NOT NULL,
    npa           TEXT NOT NULL,
    localite      TEXT NOT NULL,
    langue        TEXT NOT NULL CHECK (langue IN ('fr','de','it','en')),
    date_naissance TEXT NOT NULL,            -- ISO YYYY-MM-DD
    membre_depuis TEXT NOT NULL,
    commentaire   TEXT                        -- TEXTE LIBRE notes agents (PII mélangées : tiers, médecins, contacts...)
);

CREATE TABLE vehicules (
    vehicule_id   INTEGER PRIMARY KEY,
    plaque        TEXT NOT NULL UNIQUE,       -- ex: VD 123456, ZH 987654
    vin           TEXT NOT NULL,              -- 17 chars
    marque        TEXT NOT NULL,
    modele        TEXT NOT NULL,
    annee         INTEGER NOT NULL,
    proprietaire_id INTEGER NOT NULL REFERENCES clients(client_id)
);

CREATE TABLE contrats_assurance (
    contrat_id    INTEGER PRIMARY KEY,
    no_contrat    TEXT NOT NULL UNIQUE,       -- ASSU-2024-XXXXX
    client_id     INTEGER NOT NULL REFERENCES clients(client_id),
    type_contrat  TEXT NOT NULL CHECK (type_contrat IN ('auto','menage','voyage','assistance')),
    vehicule_id   INTEGER REFERENCES vehicules(vehicule_id),
    iban          TEXT NOT NULL,              -- CH93 0076 2011 6238 5295 7
    prime_chf     REAL NOT NULL,
    date_debut    TEXT NOT NULL,
    date_fin      TEXT NOT NULL,
    statut        TEXT NOT NULL CHECK (statut IN ('actif','suspendu','resilié'))
);

CREATE TABLE sinistres (
    sinistre_id   INTEGER PRIMARY KEY,
    no_sinistre   TEXT NOT NULL UNIQUE,       -- SIN-2025-XXXXXX
    contrat_id    INTEGER NOT NULL REFERENCES contrats_assurance(contrat_id),
    date_sinistre TEXT NOT NULL,
    montant_chf   REAL NOT NULL,
    statut        TEXT NOT NULL CHECK (statut IN ('ouvert','en_cours','clos','refusé')),
    description   TEXT NOT NULL,              -- TEXTE LIBRE avec PII
    agent_id      INTEGER REFERENCES agents(agent_id)
);

CREATE TABLE interventions_assistance (
    intervention_id INTEGER PRIMARY KEY,
    no_intervention TEXT NOT NULL UNIQUE,     -- INT-2025-XXXXXX
    client_id     INTEGER NOT NULL REFERENCES clients(client_id),
    type_inter    TEXT NOT NULL CHECK (type_inter IN ('panne_routiere','medical_etranger','rapatriement','depannage_domicile')),
    lieu          TEXT NOT NULL,              -- adresse / pays / autoroute
    date_inter    TEXT NOT NULL,
    cout_chf      REAL NOT NULL,
    notes         TEXT NOT NULL,              -- TEXTE LIBRE avec PII (médecin, contact local, etc.)
    agent_id      INTEGER REFERENCES agents(agent_id)
);

CREATE TABLE agents (
    agent_id      INTEGER PRIMARY KEY,
    nom           TEXT NOT NULL,
    prenom        TEXT NOT NULL,
    email_pro     TEXT NOT NULL,              -- prenom.nom@example.com
    departement   TEXT NOT NULL,              -- sinistres-auto, assistance-medicale, etc.
    site          TEXT NOT NULL               -- Genève, Vernier, Zurich, Bellinzone
);
"""

CLIENTS = [
    # (nom, prenom, avs, email, tel, adresse, npa, localite, langue, naissance, membre_depuis, commentaire)
    ("Dubois",     "Marie",       "756.1234.5678.97", "marie.dubois@bluewin.ch",  "+41 78 412 33 21", "Chemin des Lilas 12",      "1003", "Lausanne",   "fr", "1978-04-12", "2010-06-01",
     "Cliente VIP. Préfère être contactée le soir après 18h. Son mari Pierre Dubois (+41 78 412 33 22) gère parfois les démarches. Médecin traitant Dr. Anne Leclerc, cabinet rue de Bourg 14, Lausanne, 021 312 45 67. Allergie pénicilline notée au dossier."),
    ("Müller",     "Hans",        "756.9876.5432.10", "hans.mueller@gmx.ch",      "+41 79 555 11 22", "Bahnhofstrasse 45",        "8001", "Zürich",     "de", "1965-11-23", "1998-03-15",
     "Pensionierter Lehrer. Tochter Anna Müller (Tel +41 78 999 11 22) ist Notfallkontakt. Bevorzugt schriftliche Kommunikation. Hausarzt: Dr. med. Peter Hofmann, Praxis Limmatstrasse 200, Zürich. Hat zwei Hypotheken bei UBS, IBAN CH56 0023 0023 1234 5678 9."),
    ("Rossi",      "Giulia",      "756.4444.2222.11", "giulia.rossi@hotmail.it",  "+41 76 332 89 14", "Via Nassa 8",              "6900", "Lugano",     "it", "1985-07-08", "2015-09-22",
     "Frontaliera italiana, residenza fiscale a Como. Sorella Sofia Rossi (+39 031 234 567) come contatto in Italia. Lavora presso BSI Banca Lugano. Codice fiscale italiano: RSSGLI85L48F205X."),
    ("Favre",      "Jean-Pierre", "756.3333.1111.22", "jpfavre@gmail.com",        "+41 79 221 45 78", "Rue du Rhône 102",         "1204", "Genève",     "fr", "1959-02-19", "1985-01-10",
     "Retraité, ancien cadre Pictet & Cie. Pacemaker depuis 2022 (cardiologue Dr. François Mach, HUG). Épouse Catherine Favre, joignable +41 79 100 22 33. A signalé une procuration au profit de son fils Antoine Favre (avocat, étude Lenz & Staehelin, +41 58 450 70 00) en cas d'incapacité."),
    ("Schneider",  "Anna",        "756.5555.7777.33", "anna.schneider@swissonline.ch", "+41 78 901 23 45", "Lindenweg 7",         "3013", "Bern",       "de", "1990-09-30", "2020-11-05",
     "Junge Mutter (Sohn Lukas, geb. 2022). Partner Marc Schneider arbeitet bei der Post AG, +41 79 234 56 78. Bevorzugt Termine während Bürozeiten. Hat eine Beschwerde bei der FINMA wegen einer früheren Versicherung eingereicht (Fall-Nr. FINMA-2023-44218)."),
    ("Tavares",  "Miguel",      "756.6666.8888.44", "miguel.tavares@protonmail.com", "+41 79 333 67 89", "Avenue de France 22", "1004", "Lausanne",  "fr", "1982-12-04", "2012-08-18",
     "Indépendant, gère sa propre SARL (Tavares Consulting, IDE CHE-123.456.789). Numéro de carte de crédit professionnelle Visa Premier 4532 1234 5678 9010, exp 06/27. Conjoint Pascal Monney (couple PACS). Père malade, EMS Beau-Site Pully, demander Mme Claire Rochat à la réception."),
    ("Maillard",       "Julien",       "756.7777.9999.55", "julien.maillard@bluewin.ch",   "+41 76 555 18 27", "Chemin des Lilas 8",         "1003", "Lausanne",   "fr", "1987-09-14", "2014-02-28",
     "Travaille dans la tech (anciennement chez Swisscom, maintenant freelance). Permis B (renouvelé 2024). Sa compagne Sophie Renaud (+41 79 555 12 34) bénéficie de l'assurance ménage commune. A demandé qu'on note : ne jamais appeler avant 10h."),
    ("Weber",      "Stefan",      "756.8888.1010.66", "stefan.weber@bluemail.ch", "+41 79 666 78 90", "Hauptstrasse 33",          "4051", "Basel",      "de", "1975-08-14", "2005-07-12",
     "Chef de famille, 3 enfants. Tochter Lara Weber (16 J.) hat letztes Jahr Töfflischein gemacht. Frau Brigitte Weber-Stocker arbeitet bei Roche AG. Hausarzt Dr. Stefan Hauri, Praxis Spalentor, Basel."),
    ("Lehmann",    "Petra",       "756.9999.2020.77", "petra.lehmann@gmail.com",  "+41 78 777 89 01", "Seestrasse 88",            "8800", "Thalwil",    "de", "1980-03-27", "2018-04-09",
     "Geschieden seit 2021 (Ex-Mann Robert Lehmann nicht mehr Begünstigter — wichtig!). Kinder Mia (8) und Noah (5) bei ihr. Anwältin: Frau Dr. Susanne Kaufmann, Kanzlei Wenger Plattner, Zürich."),
    ("Bernasconi", "Marco",       "756.1111.3030.88", "marco.bernasconi@ticino.com", "+41 91 234 56 78", "Via Cattori 14",        "6612", "Ascona",     "it", "1972-10-11", "2002-06-03",
     "Imprenditore (proprietà ristorante 'Al Porto', Ascona). Coniuge Elena Caprara-Bernasconi (cliente, cl-15). Due figli adulti. Conto bancario PostFinance CH22 0900 0000 8765 4321 0 per addebito premi."),
    ("Tissot",     "Sophie",      "756.2222.4040.99", "sophie.tissot@vtxnet.ch",  "+41 79 888 90 12", "Rue Centrale 5",           "2300", "La Chaux-de-Fonds", "fr", "1992-01-17", "2021-03-14",
     "Étudiante en médecine UniNE. Apparemment colocation avec Lisa Bourquin et Théo Robert au même adresse. Boursière, demande facilités de paiement annuel."),
    ("Keller",     "Andreas",     "756.3333.5050.10", "a.keller@gmx.ch",          "+41 78 234 56 12", "Dorfstrasse 19",           "6300", "Zug",        "de", "1968-06-25", "1995-11-08",
     "CEO einer Krypto-Firma in Zug (Blockchain Valley AG, UID CHE-987.654.321). Sehr beschäftigt — Assistentin Frau Tanja Bühler (+41 41 555 12 34) bearbeitet meistens Sachen. Hat einen Porsche und eine Ducati gleichzeitig versichert."),
    ("Marchand",   "Isabelle",    "756.4444.6060.21", "isabelle.marchand@bluewin.ch", "+41 79 123 45 67", "Boulevard Helvétique 41", "1207", "Genève",  "fr", "1986-11-02", "2017-05-22",
     "Diplomate (mission permanente France auprès de l'ONU à Genève). Statut fiscal particulier (carte de légitimation DFAE type C). Conjoint Antoine Marchand-Pellet, attaché culturel. À ne PAS contacter au bureau."),
    ("Brunner",    "Markus",      "756.5555.7070.32", "markus.brunner@swissmail.ch", "+41 76 567 89 23", "Alpenstrasse 11",       "6004", "Luzern",     "de", "1955-09-08", "1980-04-17",
     "Frühpensioniert. Witwer seit 2023 (Frau Marianne verstorben — Dossier bereinigt). Tochter Christine Brunner-Imhof (+41 79 678 90 12) ist Notfallkontakt. Herzinfarkt 2024, Kardiologe Dr. Stefan Osswald, Universitätsspital Basel."),
    ("Caprara",    "Elena",       "756.6666.8080.43", "elena.caprara@gmail.com",  "+41 78 678 90 34", "Via San Gottardo 27",      "6500", "Bellinzona", "it", "1995-04-13", "2023-01-30",
     "Giovane cliente (membro dal 2023). Sorella di Marco Bernasconi (cl-10). Lavora come ingegnere alla SUPSI Manno. Fidanzato Luca Demarchi (non assicurato)."),
    ("Roulet",     "Laurent",     "756.7777.9090.54", "laurent.roulet@protonmail.com", "+41 79 789 01 45", "Chemin du Coteau 3",  "1066", "Epalinges",  "fr", "1971-12-29", "1999-09-25",
     "Architecte indépendant (atelier Roulet Architecture Sàrl, Lausanne). Trois enfants : Manon (19, étudiante EPFL), Léo (17), Camille (14). Femme Nathalie Roulet-Bachmann, infirmière au CHUV service oncologie."),
    ("Fischer",    "Ursula",      "756.8888.0101.65", "ursula.fischer@bluewin.ch", "+41 78 890 12 56", "Rosenweg 14",             "9000", "St. Gallen", "de", "1963-07-19", "1990-02-11",
     "Lehrerin Sekundarschule St. Gallen. Lebenspartner Hans-Peter Wüthrich. Hat eine Lebensversicherung Swiss Life laufen (Police-Nr. SL-987-654-321) — als Begünstigte ihre Schwester Doris Fischer-Müller eingetragen."),
    ("Janssen",    "Pieter",      "756.9999.1212.76", "pieter.janssen@expat.nl",  "+41 76 901 23 67", "Quai Wilson 35",           "1201", "Genève",     "en", "1979-05-06", "2019-08-04",
     "Dutch expat, works at WHO Geneva (UN ID badge UN-2019-44128). Wife Anneke Janssen-de Vries works at Pictet. Two kids in international school (École Internationale de Genève). Prefers English correspondence."),
    ("Bühler",     "Sandra",      "756.1010.2323.87", "sandra.buehler@gmx.ch",    "+41 79 012 34 78", "Mühlegasse 22",            "8001", "Zürich",     "de", "1984-02-28", "2011-10-15",
     "Selbstständig (Yoga-Studio 'Namaste Zürich', UID CHE-456.789.123). Ledig, keine Kinder. Bruder Daniel Bühler (+41 78 234 56 78) als Notfallkontakt. Hat 2024 Einbruch in Auto und Wohnung erlebt — sehr nervös, gut betreuen."),
    ("Henry",      "Olivier",     "756.1212.3434.98", "olivier.henry@netplus.ch", "+41 78 345 67 89", "Rue de la Gare 9",         "1920", "Martigny",   "fr", "1969-08-22", "1994-12-20",
     "Viticulteur (domaine Henry & Fils, Fully). Témoin du sinistre SIN-2025-000101 de Mme Dubois. Femme Véronique Henry-Crettenand gère la comptabilité. Trois enfants tous majeurs."),
    ("Aebi",       "Thomas",      "756.1313.4545.09", "thomas.aebi@bluewin.ch",   "+41 79 456 78 90", "Berner Strasse 67",        "3018", "Bern",       "de", "1977-11-15", "2008-06-30",
     "Polizist bei der Kantonspolizei Bern, Sektion Verkehr. Frau Heidi Aebi-Zaugg arbeitet als Krankenschwester am Inselspital. Sohn Tim (15) und Tochter Sara (12). Bevorzugt Kontakt via dienstliche E-Mail thomas.aebi@police.be.ch (auch bei Themen!)."),
    ("Krieger",    "Beatrice",    "756.1414.5656.20", "beatrice.krieger@swissmail.ch", "+41 76 567 89 01", "Sonnenweg 4",         "6010", "Kriens",     "de", "1958-03-03", "1988-09-12",
     "Witwe seit 2019 (Ehemann Walter Krieger verstorben). Lebt allein, Sohn Andreas Krieger (Arzt in München, +49 89 1234 5678) als Notfallkontakt im Ausland. Hat Diabetes Typ 2, Hausarzt Dr. Rolf Müller, Kriens."),
    ("Dos Santos", "Paulo",       "756.1515.6767.31", "paulo.dossantos@hotmail.com", "+41 78 678 90 12", "Avenue de la Praille 19", "1227", "Carouge",  "fr", "1989-10-09", "2016-04-25",
     "Portugais, permis C. Travaille comme chef de chantier Implenia (matricule employé EMP-77821). Femme Maria Dos Santos née Pereira, deux enfants (João 6 ans, Beatriz 4 ans). Famille au Portugal : mère Ana Dos Santos +351 21 234 5678."),
    ("Hofer",      "Yvonne",      "756.1616.7878.42", "yvonne.hofer@gmx.ch",      "+41 79 789 01 23", "Schulhausstrasse 30",      "4500", "Solothurn",  "de", "1981-06-18", "2013-07-19",
     "Geschieden, alleinerziehend (Sohn Tobias, 11 Jahre). Ex-Mann Marcel Hofer-Berger zahlt Alimente. Arbeitet bei der Stadt Solothurn als Sachbearbeiterin Sozialdienst. Hat ein Burn-out hinter sich (2023), aktuell stabil."),
    ("Bonnard",    "Christophe",  "756.1717.8989.53", "christophe.bonnard@bluewin.ch", "+41 78 890 12 34", "Place du Marché 2",   "1800", "Vevey",      "fr", "1973-01-25", "2003-11-08",
     "Restaurateur (établissement 'Le Lacustre', Vevey). Conjoint Michel Bonnard-Veyrat (couple enregistré). Père d'un enfant (garde partagée, ex-épouse Sandrine Bonnard née Tissot — sœur de Sophie Tissot cl-11)."),
]

AGENTS = [
    # nom, prenom, email_pro, departement, site
    ("Veuillet",   "Sylvie",      "sylvie.veuillet@example.com",    "sinistres-auto",     "Vernier"),
    ("Studer",     "Markus",      "markus.studer@example.com",      "sinistres-menage",   "Zürich"),
    ("Pinheiro",   "Ana",         "ana.pinheiro@example.com",       "assistance-medicale","Genève"),
    ("Borer",      "Jürg",        "juerg.borer@example.com",        "depannage-routier",  "Vernier"),
    ("Chappuis",   "Frédéric",    "frederic.chappuis@example.com",  "rapatriement",       "Genève"),
    ("Hänni",      "Doris",       "doris.haenni@example.com",       "sinistres-auto",     "Bern"),
    ("Galli",      "Roberto",     "roberto.galli@example.com",      "assistance-medicale","Bellinzona"),
    ("Bühlmann",   "Karin",       "karin.buehlmann@example.com",    "souscription",       "Zürich"),
]

VEHICULES = [
    # plaque, vin, marque, modele, annee, proprio_idx (1-based dans CLIENTS)
    ("VD 123456",  "WVWZZZ1KZAW123456", "Volkswagen", "Golf",        2019, 1),
    ("ZH 987654",  "WBA8E91070K123789", "BMW",        "320d",        2021, 2),
    ("TI 445566",  "ZFA31200000654321", "Fiat",       "500",         2018, 3),
    ("GE 102030",  "JT2BF22K8X0445678", "Toyota",     "Corolla",     2017, 4),
    ("BE 778899",  "WAUZZZ8K1AA901234", "Audi",       "A4 Avant",    2022, 5),
    ("VD 556677",  "JN1TBNT31U0234567", "Nissan",     "Qashqai",     2020, 6),
    ("VD 998877",  "VF1RFA00565789012", "Renault",    "Mégane",      2016, 7),
    ("BS 334455",  "WDD2050021R123456", "Mercedes",   "Classe C",    2023, 8),
    ("ZH 221133",  "VSSZZZ7NZ9R654321", "Seat",       "Alhambra",    2015, 9),
    ("TI 998877",  "ZAR94100007890123", "Alfa Romeo", "Giulietta",   2019, 10),
    ("NE 445566",  "VF7XAHMZTAZ234567", "Citroën",    "C3",          2020, 11),
    ("ZG 778899",  "WP0ZZZ99ZAS567890", "Porsche",    "911",         2024, 12),
    ("GE 334455",  "WVWZZZ6RZBU890123", "Volkswagen", "Polo",        2018, 13),
    ("LU 112233",  "WBA7C2C50FG123456", "BMW",        "X3",          2021, 14),
    ("TI 667788",  "ZFA31200000789012", "Fiat",       "Panda",       2017, 15),
    ("VD 889900",  "WAUZZZ4G7DN345678", "Audi",       "A6",          2022, 16),
    ("SG 223344",  "JTMBFREV80D012345", "Toyota",     "RAV4",        2020, 17),
    ("GE 556677",  "WV2ZZZ7HZ5H678901", "VW",         "Transporter", 2019, 18),
    ("ZH 445566",  "WMEEJ8AA8FK234567", "Smart",      "ForFour",     2016, 19),
    ("VS 112233",  "VF1KZ0J0H49890123", "Renault",    "Captur",     2021, 20),
]

CONTRATS = [
    # no_contrat, client_idx, type, vehicule_idx (None si pas auto), iban, prime, debut, fin, statut
    ("ASSU-2024-10001", 1,  "auto",       1,  "CH93 0076 2011 6238 5295 7", 1240.50, "2024-01-01", "2024-12-31", "actif"),
    ("ASSU-2024-10002", 1,  "menage",     None, "CH93 0076 2011 6238 5295 7", 380.00, "2024-01-01", "2024-12-31", "actif"),
    ("ASSU-2024-10003", 2,  "auto",       2,  "CH56 0483 5012 3456 7890 1", 1890.00, "2024-03-01", "2025-02-28", "actif"),
    ("ASSU-2024-10004", 3,  "auto",       3,  "CH47 0023 2323 4567 8901 2", 980.00,  "2024-06-15", "2025-06-14", "actif"),
    ("ASSU-2024-10005", 4,  "auto",       4,  "CH88 0900 0000 1234 5678 9", 1120.00, "2024-02-01", "2025-01-31", "actif"),
    ("ASSU-2024-10006", 4,  "voyage",     None, "CH88 0900 0000 1234 5678 9", 245.00, "2024-07-01", "2025-06-30", "actif"),
    ("ASSU-2024-10007", 5,  "auto",       5,  "CH12 0024 5678 9012 3456 7", 2150.00, "2024-09-01", "2025-08-31", "actif"),
    ("ASSU-2024-10008", 6,  "auto",       6,  "CH54 0023 4321 0987 6543 2", 1340.00, "2024-04-12", "2025-04-11", "actif"),
    ("ASSU-2023-09111", 6,  "assistance", None, "CH54 0023 4321 0987 6543 2", 165.00, "2024-01-01", "2024-12-31", "actif"),
    ("ASSU-2024-10009", 7,  "auto",       7,  "CH22 0876 5432 1098 7654 3", 890.00,  "2024-05-01", "2025-04-30", "actif"),
    ("ASSU-2024-10010", 8,  "auto",       8,  "CH77 0023 1111 2222 3333 4", 2890.00, "2024-08-15", "2025-08-14", "actif"),
    ("ASSU-2024-10011", 8,  "menage",     None, "CH77 0023 1111 2222 3333 4", 510.00, "2024-08-15", "2025-08-14", "actif"),
    ("ASSU-2023-09222", 9,  "auto",       9,  "CH99 0023 5555 6666 7777 8", 1080.00, "2024-01-01", "2024-12-31", "actif"),
    ("ASSU-2024-10012", 10, "auto",       10, "CH33 0023 8888 9999 0000 1", 1450.00, "2024-03-20", "2025-03-19", "actif"),
    ("ASSU-2024-10013", 11, "auto",       11, "CH44 0023 2222 3333 4444 5", 760.00,  "2024-04-01", "2025-03-31", "actif"),
    ("ASSU-2024-10014", 12, "auto",       12, "CH55 0023 6666 7777 8888 9", 4250.00, "2024-06-01", "2025-05-31", "actif"),
    ("ASSU-2024-10015", 13, "auto",       13, "CH66 0023 0000 1111 2222 3", 920.00,  "2024-02-15", "2025-02-14", "actif"),
    ("ASSU-2024-10016", 14, "auto",       14, "CH77 0023 4444 5555 6666 7", 2340.00, "2024-07-10", "2025-07-09", "actif"),
    ("ASSU-2024-10017", 15, "auto",       15, "CH88 0023 8888 9999 0000 1", 680.00,  "2024-09-05", "2025-09-04", "actif"),
    ("ASSU-2024-10018", 15, "voyage",     None, "CH88 0023 8888 9999 0000 1", 198.00, "2024-09-05", "2025-09-04", "actif"),
    ("ASSU-2023-09333", 16, "auto",       16, "CH11 0023 2222 3333 4444 5", 2780.00, "2023-08-01", "2024-07-31", "resilié"),
    ("ASSU-2024-10019", 17, "auto",       17, "CH22 0023 6666 7777 8888 9", 1560.00, "2024-05-20", "2025-05-19", "actif"),
    ("ASSU-2024-10020", 18, "auto",       18, "CH33 0023 0000 1111 2222 3", 1820.00, "2024-04-08", "2025-04-07", "actif"),
    ("ASSU-2024-10021", 19, "auto",       19, "CH44 0023 4444 5555 6666 7", 720.00,  "2024-08-22", "2025-08-21", "suspendu"),
    ("ASSU-2024-10022", 20, "auto",       20, "CH55 0023 8888 9999 0000 1", 1390.00, "2024-06-30", "2025-06-29", "actif"),
    ("ASSU-2024-10023", 21, "menage",     None, "CH66 0023 2222 3333 4444 5", 425.00, "2024-03-15", "2025-03-14", "actif"),
    ("ASSU-2024-10024", 22, "voyage",     None, "CH77 0023 6666 7777 8888 9", 312.00, "2024-07-01", "2025-06-30", "actif"),
    ("ASSU-2024-10025", 23, "menage",     None, "CH88 0023 0000 1111 2222 3", 490.00, "2024-09-01", "2025-08-31", "actif"),
]

SINISTRES = [
    # no_sin, contrat_idx (1-based), date, montant, statut, description, agent_idx
    ("SIN-2025-000101", 1,  "2025-03-12", 4850.00, "clos",
     "Mme Marie Dubois (tél +41 78 412 33 21) a percuté un poteau sur le parking du Migros de Lausanne. Plaque VD 123456, dégâts pare-choc avant et capot. Témoin : M. Olivier Henry. Expertise par garage Carrosserie Romand SA.", 1),
    ("SIN-2025-000102", 3,  "2025-04-05", 12300.00, "en_cours",
     "Kollision auf der A1 bei Winterthur. Hans Müller (+41 79 555 11 22) hat einen Auffahrunfall verursacht. Fahrzeug ZH 987654 mit Totalschaden hinten. Gegenpartei: Frau Sandra Bühler, Mühlegasse 22, 8001 Zürich.", 2),
    ("SIN-2025-000103", 5,  "2025-02-20", 890.00, "clos",
     "Bris de glace pare-brise sur autoroute A9 près de Sion. Jean-Pierre Favre, contrat ASSU-2024-10005, demande remplacement. Carglass Genève intervient le 22.02.2025.", 1),
    ("SIN-2025-000104", 8,  "2025-05-14", 7240.00, "en_cours",
     "Miguel Tavares (avs 756.6666.8888.44) signale vol de son véhicule VD 556677 (Nissan Qashqai) la nuit du 14.05 devant son domicile, Avenue de France 22 à Lausanne. Plainte déposée à la police cantonale, no PV LP-2025-3389.", 1),
    ("SIN-2025-000105", 10, "2025-01-30", 3120.00, "clos",
     "Julien Maillard (julien.maillard@bluewin.ch) a heurté un cerf sur la route entre Echallens et Yverdon. Véhicule VD 998877 immobilisé. Remorquage effectué par M. Jürg Borer.", 4),
    ("SIN-2025-000106", 11, "2025-06-08", 18500.00, "ouvert",
     "Dégât d'eau important au domicile de Stefan Weber, Hauptstrasse 33, 4051 Basel. Fuite chauffe-eau, parquet et meubles endommagés. Expert mandaté : Polygon AG.", 2),
    ("SIN-2025-000107", 13, "2025-04-22", 5680.00, "clos",
     "Petra Lehmann a glissé sur verglas avec sa Seat Alhambra (ZH 221133) au tunnel du Gothard. Dégâts latéraux. Constat amiable avec Mme Beatrice Krieger (passagère arrière du véhicule adverse).", 2),
    ("SIN-2025-000108", 14, "2025-03-03", 940.00, "refusé",
     "Marco Bernasconi déclare un dommage cosmétique (rayures profondes) sur sa Giulietta TI 998877 stationnée Via Cattori à Ascona. Refusé : pas couvert par la franchise.", 1),
    ("SIN-2025-000109", 16, "2025-07-19", 26800.00, "ouvert",
     "Accident grave : Andreas Keller (DOB 1968-06-25) a renversé un cycliste à Zug. Cycliste hospitalisé HUG Genève sous le nom de Patrick Romanens (transféré depuis CHUV). Procédure pénale en cours.", 6),
    ("SIN-2025-000110", 19, "2025-02-11", 1620.00, "clos",
     "Brunner Markus, Auto BMW X3 LU 112233, leichte Beschädigung beim Einparken in Tiefgarage Hotel Schweizerhof Luzern. Frontstossstange. Reparatur durch Amag Luzern.", 6),
    ("SIN-2025-000111", 22, "2025-06-25", 7900.00, "en_cours",
     "Laurent Roulet circule sur l'A1 en direction de Berne, percuté à l'arrière par un poids lourd allemand (immatriculation M-XY 4321). Sa VW Transporter GE 556677 est endommagée. IBAN remboursement : CH22 0023 6666 7777 8888 9.", 1),
    ("SIN-2025-000112", 23, "2025-08-14", 2450.00, "clos",
     "Ursula Fischer (ursula.fischer@bluewin.ch) — collision parking Coop St. Gallen. Smart ForFour SG 223344. Tiers identifié : Pieter Janssen, Quai Wilson 35, Genève.", 2),
    ("SIN-2025-000113", 25, "2025-09-02", 4180.00, "ouvert",
     "Sandra Bühler — vol effraction véhicule VW Polo GE 334455 stationné rue de Lausanne, Genève. Sac à main, ordinateur portable Apple MacBook (numéro de série C02XK1H5JG5J), passeport CH P12345678 volés.", 1),
    ("SIN-2025-000114", 4,  "2025-05-30", 690.00, "clos",
     "Giulia Rossi, plaque TI 445566 — choc à faible vitesse en marche arrière. Carrosserie Magazzini Generali Lugano. Devis accepté.", 7),
    ("SIN-2025-000115", 17, "2025-07-04", 11200.00, "en_cours",
     "Isabelle Marchand a déclaré un incendie partiel de son Audi A6 GE 102030 sur le parking du Salève. Origine probable : court-circuit batterie. Expert auto : Crash Expert SA, contact : Pierre Vuagnat 022 345 67 89.", 1),
]

INTERVENTIONS = [
    # no_int, client_idx, type, lieu, date, cout, notes, agent_idx
    ("INT-2025-000201", 1,  "panne_routiere",   "A1 sortie Lausanne-Crissier",        "2025-01-18", 280.00,
     "Marie Dubois en panne batterie, dépannage par patrouille. Arrivée 14h22, départ 14h58. Contact tél +41 78 412 33 21.", 4),
    ("INT-2025-000202", 4,  "medical_etranger", "Hôpital San Raffaele, Milan, Italie", "2025-02-08", 8400.00,
     "Jean-Pierre Favre hospitalisé suite infarctus en voyage. Contact médecin local Dr. Andrea Bianchi (+39 02 264 31 234). Rapatriement médicalisé organisé via REGA vol HB-XWA le 12.02. Personne à prévenir : son épouse Catherine Favre, +41 79 100 22 33.", 3),
    ("INT-2025-000203", 7,  "depannage_domicile","Chemin des Lilas 8, 1003 Lausanne",   "2025-03-25", 195.00,
     "Julien Maillard — serrure bloquée, intervention serrurier de garde Vieillet & Fils. Ouverture porte sans dégât.", 4),
    ("INT-2025-000204", 12, "rapatriement",     "Costa del Sol, Espagne",             "2025-04-15", 4200.00,
     "Andreas Keller accident moto à Marbella. Polyclínica Costa del Sol, Dr. Miguel Ramirez. Rapatriement sanitaire Genève. Contact urgence Mme Keller +41 78 234 56 12.", 5),
    ("INT-2025-000205", 2,  "panne_routiere",   "A3 entre Sargans et Walenstadt",      "2025-05-02", 340.00,
     "Hans Müller — courroie de distribution cassée. Remorquage vers Garage Brunner AG, Walenstadt. Véhicule ZH 987654 immobilisé 3 jours.", 4),
    ("INT-2025-000206", 14, "medical_etranger", "Hopital Necker, Paris, France",       "2025-06-12", 6700.00,
     "Markus Brunner crise cardiaque lors d'un séjour à Paris. Hospitalisation Necker. Dr. Émilie Dubois (cardio) joignable +33 1 44 49 40 00. Épouse Christine Brunner informée, voyage organisé par l'assistance.", 3),
    ("INT-2025-000207", 6,  "panne_routiere",   "Col du Grand-St-Bernard, côté suisse","2025-07-19", 520.00,
     "Miguel Tavares — surchauffe moteur en montée. Patrouille sur place 50 minutes. Pas de dépannage payant : couvert par assistance.", 4),
    ("INT-2025-000208", 19, "depannage_domicile","Mühlegasse 22, 8001 Zürich",         "2025-08-03", 165.00,
     "Sandra Bühler — fuite tuyau machine à laver. Plombier d'urgence Wasserwerker Zürich AG. Réparation et nettoyage.", 2),
    ("INT-2025-000209", 10, "medical_etranger", "Hospital Universitario, Cancún, Mexique","2025-08-22", 19800.00,
     "Marco Bernasconi accident scooter à Cancún. Fractures multiples. Médecin Dr. Carlos Mendoza (+52 998 881 0700). Rapatriement aérien organisé via vol commercial accompagnement infirmier. Famille contact : Elena Bernasconi-Caprara, sa sœur, +41 91 234 56 78.", 3),
    ("INT-2025-000210", 16, "panne_routiere",   "Autoroute A12 vers Berne",            "2025-09-10", 410.00,
     "Laurent Roulet — pneu crevé + roue de secours non disponible. Remplacement temporaire et dépannage jusqu'au garage Pneus Lausanne SA.", 4),
    ("INT-2025-000211", 3,  "rapatriement",     "Rome, Italie (gare Termini)",          "2025-04-28", 1850.00,
     "Giulia Rossi a perdu son passeport et son portefeuille. Assistance documents via consulat suisse Rome (M. Lorenzo Crivelli, +39 06 809 57 1). Avance de fonds 800 CHF + organisation retour.", 5),
]

def _iban_normalize(iban):
    return iban  # keep with spaces for realism

def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    cur.executemany(
        "INSERT INTO clients (nom, prenom, no_avs, email, telephone, adresse, npa, localite, langue, date_naissance, membre_depuis, commentaire) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        CLIENTS,
    )
    cur.executemany(
        "INSERT INTO agents (nom, prenom, email_pro, departement, site) VALUES (?,?,?,?,?)",
        AGENTS,
    )
    cur.executemany(
        "INSERT INTO vehicules (plaque, vin, marque, modele, annee, proprietaire_id) VALUES (?,?,?,?,?,?)",
        VEHICULES,
    )
    cur.executemany(
        "INSERT INTO contrats_assurance (no_contrat, client_id, type_contrat, vehicule_id, iban, prime_chf, date_debut, date_fin, statut) VALUES (?,?,?,?,?,?,?,?,?)",
        CONTRATS,
    )
    cur.executemany(
        "INSERT INTO sinistres (no_sinistre, contrat_id, date_sinistre, montant_chf, statut, description, agent_id) VALUES (?,?,?,?,?,?,?)",
        SINISTRES,
    )
    cur.executemany(
        "INSERT INTO interventions_assistance (no_intervention, client_id, type_inter, lieu, date_inter, cout_chf, notes, agent_id) VALUES (?,?,?,?,?,?,?,?)",
        INTERVENTIONS,
    )

    conn.commit()

    counts = {}
    for t in ("clients", "agents", "vehicules", "contrats_assurance", "sinistres", "interventions_assistance"):
        counts[t] = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()

    print(f"DB créée : {DB_PATH}")
    print(f"Taille  : {DB_PATH.stat().st_size / 1024:.1f} KB")
    for t, n in counts.items():
        print(f"  {t:<28} {n:>4} lignes")


if __name__ == "__main__":
    main()

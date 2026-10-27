# Euromap 77 simulátor – plán

Stav k 23. 10. 2026 (revize 3: datový model dohledán ve specifikaci, viz [Euromap77_datovy_model.md](datovy_model.md))

## Cíl a rozsah

Simulátor je OPC UA server v Pythonu, který se vůči OPC UA klientovi chová jako jeden nebo více vstřikovacích strojů podle Euromap 77 (OPC 40077 v1.01 nad OPC 40083). Slouží k vývoji a testování OPC UA klientů bez fyzických lisů. Ověřuje se UaExpertem a vlastními `asyncua` klienty, připojení skutečného MES se neplánuje.

V rozsahu:

- Všechny tři profily OPC 40077: Basic Server Profile, Jobs Server Facet a ProductionDatasetManagement Server Facet.
- Jednoduché webové UI pro ruční nastavení hodnot a pro zapnutí náhodných generátorů. Běží lokálně, bez přihlašování.
- Generátory jen pro analogové a stavové uzly. Identita, konfigurace a čítače se generovat nedají.
- Simulace výrobního cyklu: čítače, `CycleParametersEvent` a `LogbookEvent` vznikají z běhu cyklu, ne z náhody.
- Nejvýše 3 stroje najednou, každý na vlastním portu a podle svého profilu (např. „Arburg-like“, „Zhafir-like“).

Mimo rozsah:

- Věrná fyzika vstřikování.
- Řídicí API (REST apod.); simulátor se ovládá jen z UI.
- Zrychlený simulovaný čas; vše běží v reálném čase.
- Připojení a testy proti skutečnému MES nebo konektoru.
- Agregační server s více stroji pod jedním endpointem.
- Proprietární jmenné prostory výrobců.
- Euromap 79 a 82.
- Certifikace u OPC Foundation.

## Architektura a technologie

Simulace, OPC UA server a UI běží v jednom asyncio procesu a sdílejí jen State Store. Díky tomu jde každou vrstvu testovat i vyměnit samostatně.

```
 Webové UI (NiceGUI)            OPC UA klient
        │ zápis/změny                  │ opc.tcp
        ▼                              ▼
 Simulace (sim/)  ◄──►  STATE STORE  ◄──►  OPC UA server (asyncua)
        ▲                     ▲                    ▲
 Profily (YAML)          Persistence          NodeSet2 XML (DI, 40083, 40077)
```

Změna z UI nebo z generátoru jde do Store a odtud do OPC UA uzlu. Zápis z klienta jde opačnou cestou přes validaci adaptéru. Profily a NodeSety se čtou jen při startu.

## Komponenty a struktura projektu

Jádrem je centrální úložiště stavu (State Store). OPC UA vrstva, generátory, cyklus i UI do něj jen zapisují a odebírají z něj změny. Nikdo nesahá na OPC UA uzly přímo.

| Komponenta | Modul | Odpovědnost |
| --- | --- | --- |
| Načítání modelu | `opcua/nodesets.py` | Import NodeSet2 XML: DI, GeneralTypes (OPC 40083), IMM2MES (OPC 40077) |
| Builder instancí | `opcua/builder.py` | Z profilu stroje vytvoří `IMM_<Výrobce>_<SN>` pod DeviceSet, včetně `Mould_n`, `InjectionUnit_n`, `PowerUnit_n` a teplotních zón |
| Katalog uzlů | `model/registry.py` | `NodeSpec` pro každý uzel: BrowsePath, datový typ, jednotka, rozsah, RW, třída (analog/stav/…) |
| State Store | `model/state.py` | Jediný zdroj pravdy, async pub/sub změn, snapshot do JSON |
| OPC UA adaptér | `opcua/sync.py` | Store → hodnoty uzlů; zápisy klienta → validace → Store |
| Metody | `opcua/methods.py` | `SetMachineTime`, `SetMESMessage`, `StartJob`, watchdog, … |
| Události | `opcua/events.py` | Odvozený `CycleParametersEventType`, konkrétní podtypy abstraktních `LogbookEvent` typů, `Request*EventType` pro Jobs a datové sady, `MessageCondition` |
| Generátory | `sim/generators.py` | Analogové a stavové generátory, seed |
| Plánovač | `sim/engine.py` | Tick smyčka, spouští generátory podle periody |
| Cyklus | `sim/cycle.py` | Takt podle `ExpectedCycleTime`, čítače, kvalita dílu, emise eventů |
| Zakázky | `sim/jobs.py` | Fronta z `SendCyclicJobList`, stavy JobStatus |
| Datové sady | `sim/datasets.py` | Ukládání a načítání souborů přes TemporaryFileTransfer |
| Logbook | `sim/logbook.py` | Kruhový buffer ≥ 100 událostí, persistentní, HistoryRead |
| Bezpečnost | `opcua/security.py` | Certifikáty, politiky, uživatelé `OPC40077` a `OPC40077_read_only` |
| Profily | `config/*.yaml` + `config/schema.py` | Definice strojů a výchozích generátorů (pydantic) |
| UI | `ui/` | NiceGUI aplikace, stránky viz Návrh UI |

Technologie:

- **Python 3.11+.**
- **`asyncua`** jako OPC UA server. Umí import NodeSet2, metody, události i historii.
- **NiceGUI** pro webové UI. Běží ve stejné asyncio smyčce jako server, takže odpadá synchronizace vláken.
- **pydantic** a **PyYAML** pro profily, **pytest** a **pytest-asyncio** pro testy.

```
e77sim/
  nodesets/            # DI 1.04, GeneralTypes 1.02 (opravený), IMM2MES 1.01
  profiles/            # generic.yaml + předvolby pilotních strojů (extends: generic)
  e77sim/
    __main__.py        # CLI: e77sim run profiles/*.yaml --ui-port 8080
    config/  model/  opcua/  sim/  ui/
  tests/
```

## Mapování datového modelu a klasifikace uzlů

Každý uzel dostane v katalogu jednu třídu. Třída určuje, co s uzlem smí UI a co generátory: náhodu povolují jen třídy A a S.

| Třída | Co patří sem | Příklady uzlů | Ruční zápis v UI | Generátor |
| --- | --- | --- | --- | --- |
| A – analog | `AnalogItemType`, teploty, tlaky, objemy, časy | `ActualTemperature` (zóny, pohonné jednotky), `CushionVolume`, `SpecificPressureMaximum`, `InjectionTime`, `DosingTime`, `VPChangeOverPosition`, `BackPressure` | ano | ano |
| S – stav | enumerace a booleany | `MachineMode`, `ProductionStatus`, `JobStatus`, `MouldStatus`, `IsPresent`, `InProduction`, `PowerOn`, `ProductionOnlyWithMES` | ano | ano |
| C – čítač / odvozené | výsledek simulace cyklu | `JobCycleCounter`, `MachineCycleCounter`, `JobGoodPartsCounter`, `LastCycleTime`, `AverageCycleTime` | jen reset | ne |
| I – identita | statické údaje z profilu | `Manufacturer`, `Model`, `SerialNumber`, `ControllerName`, `ScrewDiameter`, `BarrelId` | ano, v profilu | ne |
| K – konfigurace MES | zapisuje MES nebo operátor | `UserMachineName`, `LocationName`, `StandstillReasons`, `MESUrl` | ano | ne |
| M – metoda | logika v simulátoru | `StartJob()`, `SetMESMessage()`, `ResetWatchDog()`, `GenerateFileForRead()` | tlačítko „zavolat“ | ne |
| E – událost | emituje cyklus nebo změna stavu | `CycleParametersEvent`, `LogbookEvent`, `MessageCondition` | tlačítko „vyvolat“ | nepřímo |

Pravidla mapování:

- **Adresace.** Uzly se adresují BrowsePath, např. `IMM_ARBURG_0123456/InjectionUnits/InjectionUnit_1/TemperatureZones/BarrelTemperatureZone_2/ActualTemperature`. NodeId instancí jsou deterministické stringy odvozené z BrowsePath (`ns=<instance>;s=IMM_ARBURG_0123456/…/ActualTemperature`). Jsou stabilní napříč restarty, takže se žádná mapa NodeId neperzistuje.
- **Analogové uzly** nesou `EngineeringUnits` (UN/CEFACT) a rozsah `EURange`. Ze stejného rozsahu bere meze i generátor.
- **Abstraktní typ `InjectionUnitCycleParametersType`** nejde instancovat přímo. Builder vytvoří odvozený typ s objekty `BarrelTemperatureZoneCycleParameters_<n>` podle počtu zón v profilu. Odvozený `CycleParametersEventType` dostane `InjectionUnitCycleParameters_<n>` a `MouldCycleParameters_<n>` podle počtu jednotek a forem.
- **Abstraktní události.** Všechny podtypy `LogbookEventType` i `Request*EventType` jsou v NodeSetu abstraktní. Simulátor pro každý použitý typ definuje konkrétní podtyp ve vlastním namespace.
- **Časy** (`InjectionTime`, `DosingTime`, `CycleTime`, …) jsou `Duration` v ms bez `EngineeringUnits`, ne analogové uzly. V katalogu patří do třídy A, ale editor je zobrazí jako čas.
- **Jednotky a rozsahy.** Specifikace jednotky nepředepisuje (`UnitId = -1`). Simulátor použije °C, cm³, bar, mm, mm/s a 1/min podle tabulky v referenci modelu.
- **Builder staví strom explicitně** uzel po uzlu, ne přes `instantiate()` z `asyncua`. Ten u odvozených typů nezkopíruje vnořené potomky a nedává string NodeId.
- **Povinné uzly** se vždy vytvoří. Volitelné jen tehdy, když je profil zapne. Tím jde simulovat i stroj, který umí jen Basic.
- **Profil stroje** (YAML) určuje identitu, počet forem, vstřikovacích jednotek a zón, zapnuté facety, výchozí hodnoty a výchozí generátory.

## Generátory a simulace cyklu

Generátor se váže k jednomu uzlu třídy A nebo S a má vlastní periodu a seed. Ruční zápis v UI generátor uzlu pozastaví, dokud ho uživatel znovu nezapne. Tak se ruční hodnota hned nepřepíše.

**Analogové generátory (třída A):**

| Generátor | Parametry | Typické použití |
| --- | --- | --- |
| Konstanta | hodnota | výchozí stav |
| Uniform | min, max | hrubý test rozsahu |
| Gauss kolem setpointu | setpoint, σ, ořez na EURange | teploty zón, polštář |
| Náhodná procházka | krok σ, min, max | pomalý drift tlaku nebo viskozity |
| Náběh 1. řádu | cíl, časová konstanta τ | nahřívání tavné komory po zapnutí |
| Sinus / pila | amplituda, perioda, offset | test grafů v MES |
| Výkyvy | pravděpodobnost, velikost | hodnoty mimo toleranci, test alarmů |

**Stavové generátory (třída S):**

| Generátor | Parametry | Typické použití |
| --- | --- | --- |
| Vážená náhoda | stavy + váhy | `MouldStatus`, booleany |
| Markovův řetězec | matice přechodů, min/max doba setrvání | `MachineMode`: AUTOMATIC ↔ SETUP ↔ MANUAL, prostoje |
| Sekvence | seznam (stav, trvání) | opakovatelný scénář pro regresní test |

Seed jde zadat pro celý stroj i pro jednotlivý generátor, takže je běh reprodukovatelný. Přechody stavů respektují závislosti: `ProductionStatus` nepřejde do výroby, když `MachineMode` není AUTOMATIC nebo když `ProductionOnlyWithMES` vyžaduje uvolnění z MES.

**Simulace cyklu.** Cyklus běží, jen když je stroj v režimu AUTOMATIC, výroba je uvolněná a existuje aktivní zakázka. Jeden cyklus proběhne takto:

1. Počká `ExpectedCycleTime` s jitterem z profilu.
2. Navzorkuje cyklové parametry z jejich generátorů. Povinné jsou `CushionVolume`, `PlastificationVolume`, `SpecificPressureMaximum`, `InjectionTime` a `DosingTime`, další podle profilu.
3. Vyhodnotí kvalitu. Dobrý díl znamená, že všechny sledované parametry jsou v toleranci. Volitelně se k tomu přičte náhodná zmetkovitost v %.
4. Navýší čítače zakázky, boxu a stroje, přepočítá `LastCycleTime` a `AverageCycleTime` a pošle `CycleParametersEvent`.
5. Po dosažení `NominalParts` ukončí zakázku a podle `ContinueAtJobEnd` pokračuje další zakázkou z fronty.

Změny `MachineMode`, `ProductionStatus` a parametrů zapsaných z UI generují odpovídající `LogbookEvent` podtypy s `EventOriginator`: MACHINE, OPERATOR nebo MES.

## Návrh UI

UI je jedna jednoduchá webová aplikace (NiceGUI), běží lokálně bez přihlašování. Nahoře je přepínač strojů (max 3), vlevo strom adresního prostoru, uprostřed editor vybraného uzlu a dole panel událostí.

1. **Přehled strojů.**
   - Karta na stroj: endpoint, počet připojených klientů, `MachineMode`, `ProductionStatus`, aktivní zakázka, poslední a průměrný čas cyklu, dobré/zmetkové díly.
   - Akce: start/stop serveru, start/stop cyklu, „vše náhodně“ a „zmrazit vše“.
2. **Strom modelu.**
   - Kopíruje OPC 40077: MachineInformation … ProductionDatasetManagement.
   - U uzlu je odznak třídy (A, S, C, I, K, M, E), ikona aktivního generátoru a aktuální hodnota.
   - Hledání podle názvu a filtr „jen A/S“.
3. **Editor uzlu.** Ovládání se mění podle třídy:
   - **A:** číslo s jednotkou v rámci EURange, výběr generátoru a jeho parametrů.
   - **S:** výběr hodnoty enumerace, výběr generátoru a jeho parametrů (váhy, přechodová matice jako tabulka).
   - **I/K:** textové pole.
   - **C:** pouze čtení a reset.
   - **M:** formulář argumentů a tlačítko „Zavolat“.
4. **Události.** Posledních N `CycleParametersEvent`, `LogbookEvent` a `MessageCondition`, ruční vyvolání alarmu s výběrem `IMMMessageClassification` a závažnosti.
5. **Zakázky a datové sady.** Fronta zakázek, ruční vložení zakázky, StartJob / InterruptJob / FinishJob, seznam datových sad se stavem Frozen/Modified.

Oproti první verzi vypadly grafy hodnot, časová osa stavů, hromadná tabulka generátorů se šablonami a správa certifikátů v UI. Šablony generátorů lze nadefinovat v profilu YAML.

## Fáze implementace

| Fáze | Obsah | Výstup |
| --- | --- | --- |
| 0 Spike | ✓ hotovo 23. 10.: import tří NodeSetů, instance `IMM_MES_InterfaceType`, odvozený event s vnořenými poli dorazí klientovi | `model/spike/cycle_event_spike.py`, zbývá prohlídka v UaExpertu |
| 1 Basic server | builder z profilu, povinné uzly, State Store, adaptér, `SetMachineTime` | Basic Server Profile v UaExpertu |
| 2 UI základ | přehled strojů, strom, editor A/S/I/K | ruční nastavování hodnot |
| 3 Generátory | analogové a stavové generátory, plánovač, výchozí generátory z profilu | náhodný běh se seedem |
| 4 Cyklus a události | cyklus, čítače, `CycleParametersEvent`, logbook s HistoryRead, `MessageCondition` | data za cyklus v UaExpertu |
| 5 Jobs facet | `SendCyclicJobList`, `ActiveJobValues`, metody a watchdog `ProductionControl` | řízení zakázek z OPC UA klienta |
| 6 Datové sady | `ProductionDatasetManagement`, TemporaryFileTransfer, Frozen/Modified | přenos sad z/do klienta |
| 7 Více strojů a bezpečnost | až 3 stroje, port na stroj, profily Arburg-like a Zhafir-like, certifikáty, role | běh tří strojů |
| 8 Testy a balení | integrační testy, Docker image, README | release 1.0 |

Pořadí je dané závislostmi: UI a generátory potřebují State Store z fáze 1. Fáze 5 a 6 lze dělat paralelně po fázi 4.

## Testování a ověření

Simulátor je hotový, když ho OPC UA klient přečte stejně jako skutečný stroj. Ověřuje se ve čtyřech vrstvách:

- **Jednotkové testy:** generátory (rozsah, seed, statistika), přechody stavů a jejich závislosti, logika cyklu a čítačů, validace zápisů.
- **Konformita modelu:**
  - Skript projde instanci a ověří, že existuje každý povinný uzel z NodeSetu.
  - Ověří, že `ServerProfileArray` obsahuje URI zapnutých profilů.
  - Ověří, že `NamespaceArray` obsahuje IMM2MES, GeneralTypes a DI.
  - Ověří, že `DeviceClass` = "Injection Moulding Machine".
- **Integrační testy:** `asyncua` klient v pytestu.
  - Subscription na `CycleParametersEvent` s kontrolou povinných polí.
  - Volání metod zakázek.
  - Přenos datové sady tam a zpět.
  - Zápis s rolí `OPC40077_read_only` musí selhat.
- **Ruční testy:**
  - Prohlídka a subscription v UaExpertu, souběžně na všech třech strojích.
  - Volitelně OPC UA Compliance Test Tool pro základní služby serveru.

## Rizika a otevřené otázky

Riziko událostí s vnořenými objekty spike 23. 10. vyřešil. Nejistá zůstává podpora alarmů (`MessageConditionType`) v `asyncua`.

| Riziko | Dopad | Opatření |
| --- | --- | --- |
| ~~Vnořené objekty v `CycleParametersEvent`~~ ověřeno spikem: fungují | – | každé pole eventu nastavovat přes `add_property` s explicitním VariantType, jinak `asyncua` pošle prázdnou hodnotu |
| `MessageConditionType` je podtyp `AlarmConditionType`; podpora alarmů a podmínek v `asyncua` je omezená (Acknowledge, ConditionRefresh) | klient neuvidí nebo nepotvrdí alarm | ověřit hned na začátku fáze 4; záloha: alarmy jen jako `MessageLogType` v logbooku |
| Verze NodeSetů: GeneralTypes 1.02 má duplicitní uzel a DI 1.05 se do `asyncua` nenaimportuje | server nenastartuje | vyřešeno: DI 1.04 a opravený GeneralTypes 1.02 uložené v `model/nodesets/` |
| Politika Basic256 je v novém OPC UA zastaralá a nemusí být v knihovně povolená | klient se nepřipojí s minimem ze specifikace | nabízet Basic256Sha256 a Basic256 jako volbu |
| Export z reálných strojů není, reálné stroje mohou vyplňovat jinou podmnožinu volitelných uzlů | klient otestovaný na simulátoru narazí u stroje na chybějící uzel | profily se drží specifikace; profil „Zhafir-like“ zapne jen povinné uzly a „Arburg-like“ i většinu volitelných, takže klient se otestuje na obou krajnostech |

Otevřené otázky:

- [x] Počet strojů: nejvýše 3, každý na vlastním portu.
- [x] Cílový MES: žádný se připojovat nebude, ověřuje se UaExpertem a `asyncua` klienty.
- [x] Export browse z reálných strojů: není, model se bere ze specifikace a NodeSetů.
- [x] UI: jednoduché, lokálně, bez přihlašování a bez API.

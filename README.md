# Euromap 77 OPC UA simulátor

Simulátor vstřikovacích lisů s rozhraním **Euromap 77 / OPC 40077 v1.01** (IMM2MES) nad OPC 40083 a OPC UA DI. Slouží k vývoji a testování OPC UA klientů bez fyzických strojů. Jeden spustitelný soubor spustí až tři stroje, každý na vlastním OPC UA portu, a k nim lokální webové UI. Stroje jde za běhu přidávat z katalogu a odebírat. V konzoli běží log a jednoduché příkazy.

Návrh a datový model jsou v [docs/plan.md](docs/plan.md) a [docs/datovy_model.md](docs/datovy_model.md).

## Spuštění

Hotový soubor pro Windows (`e77sim-<verze>-windows-x64.exe`) nebo Linux (`e77sim-<verze>-linux-x64`) stáhnete z [Releases](https://github.com/fialaste/euromap77opcuasimulator/releases). Stačí ho spustit, nic se neinstaluje:

```
e77sim.exe
```

Bez parametrů se spustí dva výchozí stroje a otevře se prohlížeč s UI. Třetí místo je volné pro stroj, který si přidáte v UI:

| Stroj | Předvolba | Endpoint |
| --- | --- | --- |
| `IMM_ARBURG_PILOT_31` | `arburg_31`: Arburg 470 E 1000-290 Golden, Basic + Jobs | `opc.tcp://<počítač>:4840/` |
| `IMM_WITTMANN_BATTENFELD_PILOT_01` | `battenfeld_1`: Battenfeld EcoPower 110/350, Basic + Jobs | `opc.tcp://<počítač>:4841/` |

Web UI běží na `http://127.0.0.1:8080/`. Konzole přijímá příkazy `s` (stav strojů), `o` (otevřít UI) a `q` (konec), simulátor ukončí i Ctrl+C.

Vlastní sestava strojů z příkazové řádky, nejvýše tři (změny strojů v UI se pak neukládají):

```
e77sim.exe -m arburg_31:4840 -m engel_26:4841 -m muj_stroj.yaml:4842
```

| Parametr | Význam |
| --- | --- |
| `-m, --machine PROFIL[:PORT]` | vestavěná předvolba (`generic`, `battenfeld_1`, `arburg_31`, `arburg_37`, `zhafir_22`, `zhafir_28`, `engel_26`, `engel_27`) nebo cesta k YAML; stejný profil lze použít víckrát, sériové číslo dostane příponu |
| `--host` | adresa OPC UA serverů, výchozí `0.0.0.0` |
| `--ui-host`, `--ui-port` | adresa a port UI, výchozí `127.0.0.1:8080` (jen lokálně) |
| `--no-browser` | neotvírat prohlížeč |
| `--seed N` | opakovatelný běh náhodných generátorů |
| `--no-security` | jen endpoint bez zabezpečení |
| `--no-anonymous` | zakázat anonymní přihlášení |
| `--password`, `--password-read-only` | hesla uživatelů `OPC40077` a `OPC40077_read_only`; prázdné heslo přijme cokoli |
| `--data-dir` | složka pro certifikáty a uložené stroje (`stroje.yaml`), výchozí `e77sim-data` vedle programu |

## Co simulátor dělá

**Adresní prostor.** Instance `IMM_<Výrobce>_<SN>` typu `IMM_MES_InterfaceType` leží pod `DeviceSet`. Strom se staví z oficiálních NodeSetů (DI 1.04, GeneralTypes 1.02, IMM2MES 1.01) uzel po uzlu včetně datových typů, ValueRank, AccessLevel a popisů ze specifikace. Povinné uzly vzniknou vždy, volitelné podle profilu. NodeId jsou deterministické stringy rovné BrowsePath od kořene stroje, například `ns=5;s=IMM_ARBURG_0123456/MachineStatus/MachineMode`. `ServerProfileArray` obsahuje URI zapnutých profilů OPC 40077.

**Hodnoty.** Analogové uzly nesou `EURange` a `EngineeringUnits` (°C, cm³, bar, mm, mm/s, 1/min podle UNECE). Skutečné teploty zón sledují `NominalTemperature` se šumem. Každý uzel třídy A (analog) nebo S (stav) může mít v UI generátor. Generátor je jen jeden a má tři parametry: nominál, odchylku a časovou konstantu. Analogová hodnota sleduje nominál (číslo nebo sousední uzel, třeba `NominalTemperature`) s časovou konstantou a přidává se k ní Gaussův šum σ. Stav stojí na nominálním stavu, s pravděpodobností danou odchylkou (za sekundu) na chvíli přejde do jiného stavu a po době dané časovou konstantou se vrátí. Fyzika se nesimuluje a čas běží reálně.

**Výrobní cyklus.** Cyklus běží, když je stroj v režimu AUTOMATIC, automatický běh je povolený, výroba je případně uvolněná z MES a zakázka je ve stavu JOB_IN_PRODUCTION. Trvá `ExpectedCycleTime` s náhodným rozptylem. Po každém cyklu se navýší čítače zakázky, krabice a stroje, přepočítá se poslední a průměrný čas cyklu a odejde `CycleParametersEvent` s vnořenými `InjectionUnitCycleParameters_<n>` (včetně teplot zón válce) a `MouldCycleParameters_<n>`. Parametry mimo toleranci dají zmetek. Po dosažení `NominalParts` zakázka skončí, pokud není `ContinueAtJobEnd`.

**Metody.** Všechny metody instance mají implementaci: `SetMachineTime`, `GetPage`/`GetCurrentPage` (PNG obrazovky), `SetMESMessage`/`ClearMESMessage`, `EnableAutomaticRun`/`DisableAutomaticRun`, `RequestTestSample`, watchdog (`SetWatchDogTime`, `ResetWatchDog`; po vypršení se zruší `ProductionReleasedByMES`), `ActivateSleepMode`/`DeactivateSleepMode`, zakázky (`SendCyclicJobList`, `SendJobList`, `SetCyclicJobData`, `StartJob`, `InterruptJob`, `FinishJob`, `StopAtCycleEnd`, resety čítačů) a datové sady (`GenerateFileForRead`/`Write`, `CloseAndCommit`, `FileType` metody přenosového souboru, `Load`, `Save`, seznamy a informace o sadách).

**Události.** Logbook posílá konkrétní podtypy abstraktních typů OPC 40077: změna parametru, uživatele, MachineMode, ProductionStatus, datové sady, zmrazení sady, důvod prostoje a hlášení. `EventOriginator` rozlišuje stroj, obsluhu v UI a MES. Události jdou na objekt stroje i na objekt Server a logbook drží posledních 1000 událostí pro HistoryRead. Z UI jde vyvolat hlášení (i s prostojem) a požadavky stroje na MES (`Request*EventType`).

**Bezpečnost (kap. 6).** Endpointy None, Basic256 a Basic256Sha256 v režimech Sign a SignAndEncrypt. Certifikát serveru se vygeneruje při prvním spuštění do `e77sim-data/pki`, klientské certifikáty se přijímají všechny. Uživatelé `OPC40077` (čtení, zápis, metody) a `OPC40077_read_only` (bez zápisu a volání metod), pro testy i anonymní přístup.

## Stroje za běhu

Tlačítko **+ Přidat stroj** otevře průvodce o třech krocích:

1. **Typ stroje**: obecný vstřikovací lis nebo předvolba pilotního stroje, k tomu výrobce, typ, sériové číslo, řízení, název v hale, pohon, počet vstřikovacích jednotek, topných zón a forem a port.
2. **Datový model**: Euromap 77 s povinným základem (sledování stroje) a dvěma rozšířeními, Zakázky (Jobs) a Datové sady (ProductionDatasetManagement). Volitelné uzly se zapínají po srozumitelných skupinách, například *Šnek a válec*, *Řízení z MES* nebo *Podrobné cyklové parametry*. Skupiny jsou v [catalog.py](e77sim/catalog.py).
3. **Shrnutí** a spuštění. Nový stroj hned běží na svém portu.

Tlačítko **Odebrat stroj** zastaví a odebere vybraný stroj, **Uložit jako YAML** stáhne jeho profil. Všechny změny se ukládají do `e77sim-data/stroje.yaml` a po dalším spuštění naběhnou znovu. Výchozí stroje jsou zabudované v programu: **Obnovit výchozí stroje** nebo smazání `stroje.yaml` vrátí původní stav. Když se simulátor spustí s `-m`, změny se neukládají.

## Webové UI

Nahoře jsou karty strojů a přehled: endpoint, počet připojených klientů, MachineMode, ProductionStatus, zakázka, čítače, časy cyklu a průběh cyklu. Tlačítka zastaví nebo spustí OPC UA server či cyklus, zapnou všechny generátory nebo je zmrazí. Vlevo je strom adresního prostoru s třídou každého uzlu (A, S, C, I, K, M) a živými hodnotami. Uprostřed se upravuje vybraný uzel: ruční hodnota, generátor, nebo volání metody s formulářem argumentů. Vpravo jsou události, nastavení cyklových parametrů, zakázky, datové sady a hlášení stroje.

## Profily

Profil je YAML s identitou stroje, zapnutými facety, skupinami volitelných uzlů (`options`), počtem a nastavením vstřikovacích jednotek, forem, teplotních zón, pohonných jednotek a obsluhy, výchozí zakázkou, cyklovými parametry a výchozími generátory. Základem je [generic.yaml](e77sim/profiles/generic.yaml) s plným Euromap 77. Předvolby pilotních strojů (`battenfeld_1`, `arburg_31`, `arburg_37`, `zhafir_22`, `zhafir_28`, `engel_26`, `engel_27`) z něj dědí přes `extends: generic` a mění jen to, co se liší. Identita a typ odpovídají seznamu pilotních strojů, počty jednotek a zón jsou odvozené z typového označení a nejsou ověřené. Pilotní předvolby mají do potvrzení rozsahu od výrobců Basic + Jobs, Zhafiry jen povinné uzly.

Starší profily s `optional: all|none` a `include`/`exclude` fungují dál. Generátory s `kind` z verze 1.0 se při načtení převedou na nominál, odchylku a časovou konstantu.

## Vývoj

```
pip install -r requirements.txt pytest pytest-asyncio
python -m e77sim            # spuštění ze zdrojů
python -m pytest            # testy: asyncua klient proti běžícímu simulátoru
pyinstaller packaging/e77sim.spec   # jeden spustitelný soubor do dist/
```

Windows `.exe` vzniká v GitHub Actions na `windows-latest`. Release vznikne pushem tagu `v*` nebo ručním spuštěním workflow *Test and build* s vyplněným `release_tag`. PyInstaller neumí křížový překlad, takže lokálně se staví vždy pro aktuální systém.

## Omezení

- Alarmy `MessageConditionType` (podtyp `AlarmConditionType`) se neposílají, protože `asyncua` podmínky a jejich potvrzování nepodporuje. Hlášení stroje jdou jako `MessageLogType` do logbooku a prostoj do `StandstillMessage`.
- Podstromy `Lock`, `Identification`, `MethodSet`, `ParameterSet` a funkční skupiny DI se nevytvářejí.
- Role se kontrolují podle uživatele, `RoleSet` serveru se nepublikuje.
- `Basic256` je v novém OPC UA zastaralý; je zapnutý jen proto, že ho specifikace uvádí jako minimum.
- Obsah datové sady je JSON se žádanými teplotami a nastavením cyklu. Formát je u reálných strojů proprietární.
- NodeSet GeneralTypes 1.02 je opravený: odstraněný duplicitní uzel a `CyclicJobListElementType` obsahuje i zděděná pole `JobListElementType`, aby šlo strukturu správně dekódovat.

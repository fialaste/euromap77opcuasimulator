# Euromap 77 OPC UA simulátor

Simulátor vstřikovacích lisů s rozhraním **Euromap 77 / OPC 40077 v1.01** (IMM2MES) nad OPC 40083 a OPC UA DI. Slouží k vývoji a testování OPC UA klientů bez fyzických strojů. Jeden spustitelný soubor spustí až tři stroje, každý na vlastním OPC UA portu, a k nim lokální webové UI. V konzoli běží log a jednoduché příkazy.

Návrh a datový model jsou v [docs/plan.md](docs/plan.md) a [docs/datovy_model.md](docs/datovy_model.md).

## Spuštění

Hotový soubor pro Windows (`e77sim-<verze>-windows-x64.exe`) nebo Linux (`e77sim-<verze>-linux-x64`) stáhnete z [Releases](https://github.com/fialaste/euromap77opcuasimulator/releases). Stačí ho spustit, nic se neinstaluje:

```
e77sim.exe
```

Bez parametrů se spustí dva stroje a otevře se prohlížeč s UI:

| Stroj | Profil | Endpoint |
| --- | --- | --- |
| `IMM_ARBURG_0123456` | Arburg-like: Basic + Jobs + ProductionDatasetManagement, většina volitelných uzlů | `opc.tcp://<počítač>:4840/` |
| `IMM_ZHAFIR_ZE1200_2026_0042` | Zhafir-like: Basic + Jobs, jen povinné uzly | `opc.tcp://<počítač>:4841/` |

Web UI běží na `http://127.0.0.1:8080/`. Konzole přijímá příkazy `s` (stav strojů), `o` (otevřít UI) a `q` (konec), simulátor ukončí i Ctrl+C.

Vlastní sestava strojů, nejvýše tři:

```
e77sim.exe -m arburg_like:4840 -m zhafir_like:4841 -m muj_stroj.yaml:4842
```

| Parametr | Význam |
| --- | --- |
| `-m, --machine PROFIL[:PORT]` | vestavěný profil (`arburg_like`, `zhafir_like`) nebo cesta k YAML; stejný profil lze použít víckrát, sériové číslo dostane příponu |
| `--host` | adresa OPC UA serverů, výchozí `0.0.0.0` |
| `--ui-host`, `--ui-port` | adresa a port UI, výchozí `127.0.0.1:8080` (jen lokálně) |
| `--no-browser` | neotvírat prohlížeč |
| `--seed N` | opakovatelný běh náhodných generátorů |
| `--no-security` | jen endpoint bez zabezpečení |
| `--no-anonymous` | zakázat anonymní přihlášení |
| `--password`, `--password-read-only` | hesla uživatelů `OPC40077` a `OPC40077_read_only`; prázdné heslo přijme cokoli |
| `--data-dir` | složka pro certifikáty, výchozí `e77sim-data` vedle programu |

## Co simulátor dělá

**Adresní prostor.** Instance `IMM_<Výrobce>_<SN>` typu `IMM_MES_InterfaceType` leží pod `DeviceSet`. Strom se staví z oficiálních NodeSetů (DI 1.04, GeneralTypes 1.02, IMM2MES 1.01) uzel po uzlu včetně datových typů, ValueRank, AccessLevel a popisů ze specifikace. Povinné uzly vzniknou vždy, volitelné podle profilu. NodeId jsou deterministické stringy rovné BrowsePath od kořene stroje, například `ns=5;s=IMM_ARBURG_0123456/MachineStatus/MachineMode`. `ServerProfileArray` obsahuje URI zapnutých profilů OPC 40077.

**Hodnoty.** Analogové uzly nesou `EURange` a `EngineeringUnits` (°C, cm³, bar, mm, mm/s, 1/min podle UNECE). Skutečné teploty zón sledují `NominalTemperature` se šumem. Každý uzel třídy A (analog) nebo S (stav) může mít v UI vlastní generátor: konstanta, uniform, gauss, náhodná procházka, náběh k cíli, sinus, vážená náhoda, Markovův řetězec, sekvence. Fyzika se nesimuluje a čas běží reálně.

**Výrobní cyklus.** Cyklus běží, když je stroj v režimu AUTOMATIC, automatický běh je povolený, výroba je případně uvolněná z MES a zakázka je ve stavu JOB_IN_PRODUCTION. Trvá `ExpectedCycleTime` s náhodným rozptylem. Po každém cyklu se navýší čítače zakázky, krabice a stroje, přepočítá se poslední a průměrný čas cyklu a odejde `CycleParametersEvent` s vnořenými `InjectionUnitCycleParameters_<n>` (včetně teplot zón válce) a `MouldCycleParameters_<n>`. Parametry mimo toleranci dají zmetek. Po dosažení `NominalParts` zakázka skončí, pokud není `ContinueAtJobEnd`.

**Metody.** Všechny metody instance mají implementaci: `SetMachineTime`, `GetPage`/`GetCurrentPage` (PNG obrazovky), `SetMESMessage`/`ClearMESMessage`, `EnableAutomaticRun`/`DisableAutomaticRun`, `RequestTestSample`, watchdog (`SetWatchDogTime`, `ResetWatchDog`; po vypršení se zruší `ProductionReleasedByMES`), `ActivateSleepMode`/`DeactivateSleepMode`, zakázky (`SendCyclicJobList`, `SendJobList`, `SetCyclicJobData`, `StartJob`, `InterruptJob`, `FinishJob`, `StopAtCycleEnd`, resety čítačů) a datové sady (`GenerateFileForRead`/`Write`, `CloseAndCommit`, `FileType` metody přenosového souboru, `Load`, `Save`, seznamy a informace o sadách).

**Události.** Logbook posílá konkrétní podtypy abstraktních typů OPC 40077: změna parametru, uživatele, MachineMode, ProductionStatus, datové sady, zmrazení sady, důvod prostoje a hlášení. `EventOriginator` rozlišuje stroj, obsluhu v UI a MES. Události jdou na objekt stroje i na objekt Server a logbook drží posledních 1000 událostí pro HistoryRead. Z UI jde vyvolat hlášení (i s prostojem) a požadavky stroje na MES (`Request*EventType`).

**Bezpečnost (kap. 6).** Endpointy None, Basic256 a Basic256Sha256 v režimech Sign a SignAndEncrypt. Certifikát serveru se vygeneruje při prvním spuštění do `e77sim-data/pki`, klientské certifikáty se přijímají všechny. Uživatelé `OPC40077` (čtení, zápis, metody) a `OPC40077_read_only` (bez zápisu a volání metod), pro testy i anonymní přístup.

## Webové UI

Nahoře jsou karty strojů a přehled: endpoint, počet připojených klientů, MachineMode, ProductionStatus, zakázka, čítače, časy cyklu a průběh cyklu. Tlačítka zastaví nebo spustí OPC UA server či cyklus, zapnou všechny generátory nebo je zmrazí. Vlevo je strom adresního prostoru s třídou každého uzlu (A, S, C, I, K, M) a živými hodnotami. Uprostřed se upravuje vybraný uzel: ruční hodnota, generátor, nebo volání metody s formulářem argumentů. Vpravo jsou události, nastavení cyklových parametrů, zakázky, datové sady a hlášení stroje.

## Profily

Profil je YAML s identitou stroje, zapnutými facety, výběrem volitelných uzlů, počtem a nastavením vstřikovacích jednotek, forem, teplotních zón, pohonných jednotek a obsluhy, výchozí zakázkou, cyklovými parametry a výchozími generátory. Vzorem jsou [arburg_like.yaml](e77sim/profiles/arburg_like.yaml) a [zhafir_like.yaml](e77sim/profiles/zhafir_like.yaml). Hodnoty v nich odpovídají běžné praxi, nejsou z exportu reálného stroje.

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

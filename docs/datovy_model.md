# Euromap 77 – datový model pro simulátor

Stav k 23. 10. 2026. Výtah z oficiálních NodeSetů OPC Foundation a z textu specifikace na reference.opcfoundation.org. Export z reálných strojů není k dispozici, takže „věrnost“ znamená věrnost specifikaci OPC 40077 v1.01.

## Zdroje a verze

| Model | Verze | Soubor | Poznámka |
| --- | --- | --- | --- |
| OPC 40077 IMM2MES | 1.01 (2020-06-01) | `nodesets/Opc.Ua.PlasticsRubber.IMM2MES.NodeSet2.xml` | Vyžaduje GeneralTypes 1.02 a DI 1.02. |
| OPC 40083 GeneralTypes | 1.02 (2020-06-01) | `nodesets/Opc.Ua.PlasticsRubber.GeneralTypes.NodeSet2.xml` | **Opravený:** originál má u `ActualTemperature` (ns=1;i=6017) dvakrát `EngineeringUnits` (i=6042 a i=6711) a `asyncua` ho odmítne. Duplicitní i=6711 je odstraněný. |
| OPC 10000-100 DI | 1.04.0 (2022-11-03) | `nodesets/Opc.Ua.Di.NodeSet2.xml` | Aktuální DI 1.05 se do `asyncua` 2.1 nenaimportuje (BadParentNodeIdInvalid), 1.04 funguje. |

Zdroj NodeSetů: [UA-Nodeset na GitHubu](https://github.com/OPCFoundation/UA-Nodeset). Text specifikace: [OPC 40077](https://reference.opcfoundation.org/specs/OPC-40077), zejména [6 Bezpečnost](https://reference.opcfoundation.org/specs/OPC-40077/6), [17.3 InjectionUnitCycleParametersType](https://reference.opcfoundation.org/specs/OPC-40077/17.3) a [19 Profily](https://reference.opcfoundation.org/specs/OPC-40077/19).

## Ověřeno spikem v asyncua 2.1

Skript `spike/cycle_event_spike.py`:

- Všechny tři NodeSety se naimportují (DI 1.04, opravené GeneralTypes 1.02, IMM2MES 1.01).
- `IMM_MES_InterfaceType` jde instancovat pod `DeviceSet`, jen s povinnými uzly je to 174 uzlů.
- Odvozený `CycleParametersEventType` s vnořeným `InjectionUnitCycleParameters_1/CushionVolume` dorazí klientovi přes subscription včetně `EngineeringUnits`. Největší riziko z plánu je tím vyřešené.
- Dvě úskalí:
  - `asyncua` u polí eventu převzatých z NodeSetu nezná datový typ (VariantType Null) a klientovi pošle prázdnou hodnotu. Každé pole se proto musí nastavit přes `event.add_property(jméno, hodnota, VariantType)`.
  - Instancování odvozeného typu nezkopíruje potomky vnořených objektů o dvě úrovně níž (`BarrelTemperatureZoneCycleParameters_1` vznikne prázdný). Builder proto musí stavět strom explicitně uzel po uzlu. To stejně chceme kvůli string NodeId.

## Profily a facety

| Profil | URI do `ServerProfileArray` |
| --- | --- |
| Basic Server Profile | `http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/Basic` |
| Jobs Server Facet | `http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/Jobs` |
| ProductionDatasetManagement Server Facet | `http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/ProductionDatasetManagement` |

Basic dále vyžaduje ComplexType, Standard Event Subscription a Method Server Facet z OPC UA Part 7 a BaseDevice_Server_Facet z DI.

## Bezpečnost podle specifikace (kap. 6)

- Aplikační autentizace certifikáty X.509, stačí self-signed s ruční trust listem.
- Minimum: politika **Basic256**, režim **Sign**.
- Uživatelé `OPC40077` (role `OPC40077`, zápis vybraných parametrů) a `OPC40077_read_only` (bez zápisu). Hesla určuje výrobce, mohou být prázdná.
- Vlastní role výrobce nesmí začínat na `OPC40077`. Anonymní přístup je dovolený pro testy.

## Struktura instance `IMM_MES_InterfaceType`

Instance se jmenuje `IMM_<Výrobce>_<SN>` a leží pod `DeviceSet`. Značky: M = povinné, O = volitelné, 0..n = placeholder (`_<Nr>` od 1). U analogových uzlů je `EURange` vždy povinný, `EngineeringUnits` podle značky.

Podstromy `Lock`, `<GroupIdentifier>`, `Identification`, `MethodSet`, `ParameterSet` a vnitřek `<TransferState>` jsou zkrácené. Simulátor je nevytváří.

- **InjectionUnits** (InjectionUnitsType) [M]
  - NodeVersion: String [M]
  - **InjectionUnit_<Nr>** (InjectionUnitType) [0..n]
    - BarrelId: String [M]
    - InProduction: Boolean [M]
    - Index: UInt32 [M]
    - IsPresent: Boolean [M]
    - MaxScrewStroke: Double [O] · Analog (EURange, EU M)
    - ScrewDiameter: Double [O] · Analog (EURange, EU M)
    - ScrewId: String [O]
    - ScrewVolume: Double [O] · Analog (EURange, EU M)
    - **TemperatureZones** (TemperatureZonesType) [M]
      - NodeVersion: String [M]
      - **<TemperatureZone_Nr>** (TemperatureZoneType) [0..n]
        - ActualTemperature: Double [M] · Analog (EURange, EU O)
        - Classification: TemperatureZoneClassificationEnumeration [O]
        - ControlMode: ControlModeEnumeration [M]
        - HighDeviationTemperature1: Double [O] · Analog (EURange, EU O)
        - HighDeviationTemperature2: Double [O] · Analog (EURange, EU O)
        - Index: UInt32 [M]
        - IsPresent: Boolean [M]
        - LowDeviationTemperature1: Double [O] · Analog (EURange, EU O)
        - LowDeviationTemperature2: Double [O] · Analog (EURange, EU O)
        - Name: String [M]
        - NominalTemperature: Double [M] · Analog (EURange, EU O)
        - StandbyTemperature: Double [O] · Analog (EURange, EU O)
- **Jobs** (JobsType) [O]
  - **ActiveJob** (CyclicJobInformationType) [M]
    - ContinueAtJobEnd: Boolean [M]
    - CustomerName: String [M]
    - ExpectedCycleTime: Duration [O]
    - JobDescription: String [M]
    - JobName: String [M]
    - Material: String[] [M]
    - MouldId: String [O]
    - NominalBoxParts: UInt64 [O]
    - NominalParts: UInt64 [M]
    - NumCavities: UInt32 [O]
    - ProductDescription: String[] [M]
    - ProductName: String[] [M]
    - ProductionDatasetDescription: String [M]
    - ProductionDatasetName: String [M]
    - `SetCyclicJobData()` metoda [M]
  - **ActiveJobValues** (ActiveCyclicJobValuesType) [M]
    - AverageCycleTime: Duration [O] · BaseDataVariableType
    - BoxBadPartsCounter: UInt64 [O] · BaseDataVariableType
    - BoxCycleCounter: UInt64 [O] · BaseDataVariableType
    - BoxGoodPartsCounter: UInt64 [O] · BaseDataVariableType
    - BoxId: String [O]
    - BoxPartsCounter: UInt64 [O] · BaseDataVariableType
    - BoxTestSamplesCounter: UInt64 [O] · BaseDataVariableType
    - CurrentLotName: String [M]
    - JobBadPartsCounter: UInt64 [M] · BaseDataVariableType
    - JobCycleCounter: UInt64 [M] · BaseDataVariableType
    - JobGoodPartsCounter: UInt64 [M] · BaseDataVariableType
    - JobPartsCounter: UInt64 [M] · BaseDataVariableType
    - JobStatus: JobStatusEnumeration [M]
    - JobTestSamplesCounter: UInt64 [M] · BaseDataVariableType
    - LastCycleTime: Duration [M] · BaseDataVariableType
    - LastPartId: String[] [O] · BaseDataVariableType
    - MachineCycleCounter: UInt64 [O] · BaseDataVariableType
    - `FinishJob()` metoda [M]
    - `InterruptJob()` metoda [M]
    - `ResetAverageCycleTime()` metoda [O]
    - `ResetBoxCounters()` metoda [O]
    - `ResetJobCounters()` metoda [M]
    - `StartJob()` metoda [M]
    - `StopAtCycleEnd()` metoda [M]
  - **JobInPreparation** (CyclicJobInformationType) [O]
    - ContinueAtJobEnd: Boolean [M]
    - CustomerName: String [M]
    - ExpectedCycleTime: Duration [O]
    - JobDescription: String [M]
    - JobName: String [M]
    - Material: String[] [M]
    - MouldId: String [O]
    - NominalBoxParts: UInt64 [O]
    - NominalParts: UInt64 [M]
    - NumCavities: UInt32 [O]
    - ProductDescription: String[] [M]
    - ProductName: String[] [M]
    - ProductionDatasetDescription: String [M]
    - ProductionDatasetName: String [M]
    - `SetCyclicJobData()` metoda [M]
  - `SendCyclicJobList()` metoda [O]
  - `SendJobList()` metoda [O]
- **MachineConfiguration** (MachineConfigurationType) [M]
  - LocationName: String [M]
  - PageDirectory: PageEntryDataType[] [O]
  - TimeZoneOffset: TimeZoneDataType [M]
  - UserMachineName: String [M]
  - `GetCurrentPage()` metoda [O]
  - `GetPage()` metoda [O]
  - `SetMachineTime()` metoda [M]
- **MachineInformation** (MachineInformationType) [M]
  - AssetId: String [O]
  - ComponentName: LocalizedText [O]
  - ControllerName: String [M]
  - DeviceClass: String [M]
  - DeviceManual: String [O]
  - DeviceRevision: String [O]
  - HardwareRevision: String [O]
  - Manufacturer: LocalizedText [M]
  - ManufacturerUri: String [O]
  - Model: LocalizedText [M]
  - ProductCode: String [O]
  - ProductInstanceUri: String [O]
  - RevisionCounter: Int32 [O]
  - SerialNumber: String [M]
  - SoftwareRevision: String [O]
  - SupportedLogbookEvents: LogbookEventsEnumeration[] [M]
  - **<GroupIdentifier>** (FunctionalGroupType) [0..n]
  - **Identification** (FunctionalGroupType) [O]
  - **Lock** (LockingServicesType) [O]
  - **MethodSet** (BaseObjectType) [O]
  - **ParameterSet** (BaseObjectType) [O]
- **MachineMESConfiguration** (MachineMESConfigurationType) [M]
  - MESUrl: String [O]
  - StandstillReasons: StandstillReasonType[] [M]
  - StandstillReasonsLockedByMES: Boolean [M]
- **MachineMESStatus** (MachineMESStatusType) [M]
  - StandstillReasonId: String [M]
  - **MESMessage** (MESMessageType) [M]
    - Id: String [M]
    - Message: String [M]
    - Severity: UInt16 [M]
  - **ProductionControl** (ProductionControlType) [M]
    - AutomaticRunEnabled: Boolean [M]
    - ProductionOnlyWithMES: Boolean [O]
    - ProductionReleasedByMES: Boolean [M]
    - ProductionStatus: ProductionStatusEnumeration [M]
    - `DisableAutomaticRun()` metoda [M]
    - `EnableAutomaticRun()` metoda [M]
    - `RequestTestSample()` metoda [O]
    - `ResetWatchDog()` metoda [O]
    - `SetWatchDogTime()` metoda [O]
  - **StandstillMessage** (StandstillMessageType) [M]
    - Classification: Enumeration [M]
    - Id: String [M]
    - Message: LocalizedText [M]
    - Severity: UInt16 [M]
  - `ClearMESMessage()` metoda [M]
  - `SetMESMessage()` metoda [M]
- **MachineStatus** (MachineStatusType) [M]
  - IsPresent: Boolean [M]
  - MachineMode: MachineModeEnumeration [M]
  - **Users** (UsersType) [M]
    - NodeVersion: String [M]
    - **User_<Nr>** (UserType) [0..n]
      - CardUid: String [O]
      - Id: String [M]
      - IsPresent: Boolean [M]
      - Language: LocaleId [O]
      - Name: String [M]
      - UserLevel: String [O]
      - UserRole: String [O]
  - `ActivateSleepMode()` metoda [O]
  - `DeactivateSleepMode()` metoda [O]
- **Moulds** (MouldsType) [M]
  - NodeVersion: String [M]
  - **Mould_<Nr>** (MouldType) [0..n]
    - Description: String [M]
    - Id: String [M]
    - Index: UInt32 [M]
    - IsPresent: Boolean [M]
    - MouldStatus: MouldStatusEnumeration [M]
    - **TemperatureZones** (TemperatureZonesType) [M]
      - NodeVersion: String [M]
      - **<TemperatureZone_Nr>** (TemperatureZoneType) [0..n]
        - ActualTemperature: Double [M] · Analog (EURange, EU O)
        - Classification: TemperatureZoneClassificationEnumeration [O]
        - ControlMode: ControlModeEnumeration [M]
        - HighDeviationTemperature1: Double [O] · Analog (EURange, EU O)
        - HighDeviationTemperature2: Double [O] · Analog (EURange, EU O)
        - Index: UInt32 [M]
        - IsPresent: Boolean [M]
        - LowDeviationTemperature1: Double [O] · Analog (EURange, EU O)
        - LowDeviationTemperature2: Double [O] · Analog (EURange, EU O)
        - Name: String [M]
        - NominalTemperature: Double [M] · Analog (EURange, EU O)
        - StandbyTemperature: Double [O] · Analog (EURange, EU O)
- **PowerUnits** (PowerUnitsType) [M]
  - NodeVersion: String [M]
  - **PowerUnit_<Nr>** (PowerUnitType) [0..n]
    - ActualTemperature: Double [O] · Analog (EURange, EU M)
    - Id: String [M]
    - Index: UInt32 [M]
    - IsPresent: Boolean [M]
    - PowerOn: Boolean [M]
- **ProductionDatasetManagement** (ProductionDatasetManagementType) [O]
  - **ActiveProductionDatasetStatus** (ProductionDatasetStatusType) [M]
    - Frozen: Boolean [O]
    - Information: ProductionDatasetInformationType [M]
    - Modified: Boolean [O]
    - `Load()` metoda [O]
    - `Save()` metoda [O]
  - **ProductionDatasetInPreparationStatus** (ProductionDatasetStatusType) [O]
    - Frozen: Boolean [O]
    - Information: ProductionDatasetInformationType [M]
    - Modified: Boolean [O]
    - `Load()` metoda [O]
    - `Save()` metoda [O]
  - **ProductionDatasetLists** (ProductionDatasetListsType) [O]
    - `GetProductionDatasetList()` metoda [M]
    - `SendProductionDatasetList()` metoda [M]
  - **ProductionDatasetTransfer** (TemporaryFileTransferType) [M]
    - ClientProcessingTimeout: Duration [M]
    - **<TransferState>** (FileTransferStateMachineType) [0..n]
    - `CloseAndCommit()` metoda [M]
    - `GenerateFileForRead()` metoda [M]
    - `GenerateFileForWrite()` metoda [M]
  - `GetProductionDatasetInformation()` metoda [O]
  - `SendProductionDatasetInformation()` metoda [O]

## Události

Všechny typy logbooku a request eventy jsou v NodeSetu **abstraktní**. Simulátor proto musí pro každý typ, který posílá, definovat konkrétní podtyp ve vlastním namespace (např. `SimMachineModeChangeLogType`). `LogbookEventType` má vnořený objekt `User` (UserType), který se plní stejně jako vnořená pole cyklových parametrů.

Odvozený `CycleParametersEventType` (podle 17.3) navíc obsahuje:

- `InjectionUnitCycleParameters_<Nr>`: odvozený typ od abstraktního `InjectionUnitCycleParametersType` (proměnné viz níže).
- `BarrelTemperatureZoneCycleParameters_<Nr>` uvnitř každé vstřikovací jednotky: `TemperatureZoneCycleParametersType` s `ActualTemperature` (Analog, M), `Index` (M), `Name` (M) a `Classification` (O).
- `MouldCycleParameters_<Nr>`: podtyp `MouldCycleParametersType`, který má jen `Index`. Teplotní zóny formy se přidávají stejně jako u válce.

`IMM_MES_InterfaceType` deklaruje GeneratesEvent: CycleParametersEventType, LogbookEventType.

- **CycleParametersEventType** (abstraktní) ← BaseEventType: AverageCycleTime: Duration [O], BoxBadPartsCounter: UInt64 [O], BoxCycleCounter: UInt64 [O], BoxGoodPartsCounter: UInt64 [O], BoxId: String [O], BoxPartsCounter: UInt64 [O], BoxTestSamplesCounter: UInt64 [O], CavityCycleQuality: CavityCycleQualityEnumeration[] [O], CurrentLotName: String [M], CycleQuality: CycleQualityEnumeration [M], CycleTime: Duration [M], JobBadPartsCounter: UInt64 [M], JobCycleCounter: UInt64 [M], JobGoodPartsCounter: UInt64 [M], JobName: String [M], JobPartsCounter: UInt64 [M], JobStatus: JobStatusEnumeration [M], JobTestSamplesCounter: UInt64 [M], MachineCycleCounter: UInt64 [O], PartId: String[] [O]
- **LogbookEventType** (abstraktní) ← BaseEventType: EventOriginator: EventOriginatorEnumeration [M], JobCycleCounter: UInt64 [O], User: UserType [M]
- **MachineModeChangeLogType** (abstraktní) ← LogbookEventType: NewMachineMode: MachineModeEnumeration [M], OldMachineMode: MachineModeEnumeration [M]
- **MessageConditionType** ← AlarmConditionType: Classification: Enumeration [M], Id: String [M], IsStandstillMessage: Boolean [M]
- **MessageLogType** (abstraktní) ← LogbookEventType: Classification: Enumeration [M], Id: String [M], IsStandstillMessage: Boolean [M]
- **ParameterChangeLogType** (abstraktní) ← LogbookEventType: NewValue: PropertyType [M], NewValueUnit: EUInformation [O], OldValue: PropertyType [M], OldValueUnit: EUInformation [O], ParameterId: String [M]
- **ProductionDatasetChangeLogType** (abstraktní) ← LogbookEventType: NewProductionDatasetName: String [M], OldProductionDatasetName: String [M]
- **ProductionDatasetFrozenLogType** (abstraktní) ← LogbookEventType: NewValue: Boolean [M], OldValue: Boolean [M]
- **ProductionStatusChangeLogType** (abstraktní) ← LogbookEventType: NewProductionStatus: ProductionStatusEnumeration [M], OldProductionStatus: ProductionStatusEnumeration [M]
- **RemoteAccessLogType** (abstraktní) ← LogbookEventType: Origin: String [O], RemoteUserName: String [M], UserChange: UserChangeEnumeration [M]
- **RequestAddMaterialEventType** (abstraktní) ← BaseEventType: Id: String [M]
- **RequestCyclicJobListEventType** (abstraktní) ← BaseEventType: —
- **RequestCyclicJobWriteEventType** (abstraktní) ← BaseEventType: JobName: String [M]
- **RequestJobListEventType** (abstraktní) ← BaseEventType: —
- **RequestProductionDatasetListEventType** (abstraktní) ← BaseEventType: MouldId: String [M], NameFilter: String [M]
- **RequestProductionDatasetReadEventType** (abstraktní) ← BaseEventType: Name: String [M], Storage: StorageEnumeration [M]
- **RequestProductionDatasetWriteEventType** (abstraktní) ← BaseEventType: Components: UInt16[] [M], Name: String [M], Storage: StorageEnumeration [M]
- **SequenceChangeLogType** (abstraktní) ← LogbookEventType: SequenceChange: SequenceChangeEnumeration [M]
- **StandstillReasonLogType** (abstraktní) ← LogbookEventType: StandstillReasonId: String [M]
- **UserFeedbackLogType** (abstraktní) ← LogbookEventType: Classification: Enumeration [M]
- **UserLogType** (abstraktní) ← LogbookEventType: UserChange: UserChangeEnumeration [M]

### InjectionUnitCycleParametersType (abstraktní)

Povinné: `Index`, `CushionVolume`, `PlastificationVolume`, `SpecificPressureMaximum` (Analog) a `InjectionTime`, `DosingTime` (Duration v ms, bez EU).

Volitelné (Analog): `CushionStroke`, `DecompressionVolumeBeforePlastification`, `DecompressionVolumeAfterPlastification`, `HydraulicPressureMaximum`, `PlastificationRotationalSpeedMaximum/Average`, `PlastificationCircumferentialSpeedMaximum/Average`, `InjectionSpeedMaximum/Average`, `TransferVolume`, `TransferStroke`, `HoldHydraulicPressureMaximum/Average`, `HoldSpecificPressureMaximum/Average`, `CavityPressureMaximum`, `PlastificationHydraulicPressureMaximum/Average`, `PlastificationSpecificPressureMaximum/Average`, `TransferHydraulicPressure`, `TransferSpecificPressure`, `TransferCavityPressure`, `BackPressure`, `FlowIndex`, `InjectionStartPosition`, `VPChangeOverPosition`.

## Výčtové typy (Enumeration)

- **CavityCycleQualityEnumeration**: 0=NO_PART, 1=GOOD_PART, 2=BAD_PART, 3=REWORK
- **ControlModeEnumeration**: 0=OTHER, 1=OFF, 2=AUTOMATIC, 3=TUNING, 4=STANDBY, 5=OPEN_LOOP, 6=ONLY_MEASUREMENT
- **CycleQualityEnumeration**: 0=GOOD_CYCLE, 1=BAD_CYCLE, 2=TEST_SAMPLE_CYCLE, 3=FAILED_CYCLE
- **EventOriginatorEnumeration**: 0=OTHER, 1=MACHINE, 2=OPERATOR, 3=MES, 4=PERIPHERAL_DEVICE
- **IMMMessageClassificationEnumeration**: 0=OTHER, 100=IMM_INJECTION_UNIT, 101=IMM_CLAMPING_UNIT, 102=IMM_HARDWARE, 103=IMM_COMPRESSED_AIR_CONTROL, 104=IMM_MACHINE_MONITORING, 105=IMM_MOULD, 106=IMM_EJECTOR, 107=IMM_CORE_PULL, 108=IMM_TABLE, 109=IMM_INJECTION_PROGRAM, 110=IMM_HYDRAULIC_TEMPERATURE_CONTROL, 111=IMM_CYLINDER_TEMPERATURE_CONTROL, 112=IMM_MOULD_TEMPERATURE_CONTROL, 113=IMM_HOT_RUNNER, 114=IMM_INTERFACES, 115=IMM_MEASURING_SYSTEM, 116=IMM_ROBOTIC_SYSTEM_INTERFACE, 117=IMM_SPECIAL_PURPOSE_SIGNALS, 118=IMM_REAL_TIME_ETHERNET_SYSTEM, 119=IMM_MACHINE_CONTROLLER, 120=IMM_SOFTWARE_MONITORING, 200=PERIPHERAL_EXTERNAL_DEVICE_INTERFACE, 201=PERIPHERAL_TEMPERATURE_CONTROL_UNIT, 202=PERIPHERAL_ROBOTICS_SYSTEM, 203=PERIPHERAL_LSR, 204=PERIPHERAL_STRIPPER_UNIT, 205=PERIPHERAL_DRYER, 206=PERIPHERAL_CONVEYOR_BELT, 207=PERIPHERAL_SORTER_UNIT, 208=PERIPHERAL_COLOURING_UNIT, 209=PERIPHERAL_FEEDING, 210=PERIPHERAL_EXTERNAL_ALARMS, 211=PERIPHERAL_VACUUM_CONTROL, 212=PERIPHERAL_PRINTER_INTERFACE, 300=OPERATION_QUALITY_MONITORING, 301=OPERATION_MANUAL_OPERATION, 302=OPERATION_EMERGENCY_STOP, 303=OPERATION_JOB_STATUS
- **JobStatusEnumeration**: 0=OTHER, 1=TRANSFERRED_ASSIGNED, 2=SET_UP_ACTIVE, 3=SET_UP_INTERRUPTED, 4=SET_UP_FINISHED, 5=START_UP_ACTIVE, 6=JOB_IN_PRODUCTION, 7=JOB_INTERRUPTED, 8=JOB_FINISHED, 9=TEAR_DOWN_ACTIVE, 10=TEAR_DOWN_INTERRUPTED, 11=TEAR_DOWN_FINISHED
- **LogbookEventsEnumeration**: 0=PARAMETER_CHANGE, 1=USER, 2=REMOTE_ACCESS, 3=SEQUENCE_CHANGE, 4=MACHINE_MODE_CHANGE, 5=PRODUCTION_STATUS_CHANGE, 6=PRODUCTION_DATASET_CHANGE, 7=PRODUCTION_DATASET_FROZEN, 8=STANDSTILL_REASON, 9=MESSAGE, 10=USER_FEEDBACK
- **MachineModeEnumeration**: 0=OTHER, 1=AUTOMATIC, 2=SEMI_AUTOMATIC, 3=MANUAL, 4=SETUP, 5=SLEEP
- **MouldStatusEnumeration**: 0=OTHER, 1=MOULD_NOT_INSTALLED, 2=MOULD_CHANGE, 3=MOULD_INSTALLED
- **ProductionStatusEnumeration**: 0=OTHER, 1=NO_PRODUCTION, 2=START_UP, 3=READY_FOR_PRODUCTION, 4=PRODUCTION, 5=DRY_RUN
- **SequenceChangeEnumeration**: 0=UPDATE, 1=ADD, 2=MODIFY, 3=MOVE, 4=DELETE
- **StorageEnumeration**: 1=PRODUCTION, 2=PREPARATION, 4=FILE_SYSTEM
- **TemperatureZoneClassificationEnumeration**: 0=OTHER, 1=HEATING, 2=COOLING, 3=TEMPERATURE_CONTROL, 4=HOT_RUNNER, 5=MEASURING
- **UserChangeEnumeration**: 0=LOG_ON, 1=LOG_OFF

## Strukturované typy

- **CyclicJobListElementType**: NominalParts: UInt64, NominalBoxParts: UInt64, ExpectedCycleTime: Duration, MouldId: String, NumCavities: UInt32
- **JobListElementType**: JobName: String, JobDescription: String, JobClassification: String, CustomerName: String, ProductionDatasetName: String, ProductionDatasetDescription: String, Material: String, ProductName: String, ProductDescription: String, JobPriority: String, PlannedStart: DateTime, PlannedProductionTime: Duration, LatestEnd: DateTime
- **PageEntryDataType**: Id: String, Title: LocalizedText
- **ProductionDatasetInformationType**: Name: String, Description: String, MESId: String, CreationTimestamp: DateTime, LastModificationTimestamp: DateTime, LastSaveTimestamp: DateTime, UserName: String, Components: UInt16, Manufacturer: String, SerialNumber: String, Model: String, ControllerName: String, UserMachineName: String, LocationName: String, ProductName: String, MouldId: String, NumCavities: UInt32
- **ProductionDatasetReadOptionsType**: Storage: StorageEnumeration, Name: String
- **ProductionDatasetWriteOptionsType**: Storage: StorageEnumeration, Name: String, Components: UInt16
- **StandstillReasonType**: Id: String, Text: LocalizedText, LockedByMES: Boolean

## Metody a jejich argumenty

- **ActivateSleepMode**()
- **ClearMESMessage**()
- **Close**(FileHandle: UInt32)
- **CloseAndCommit**(FileHandle: UInt32) → CompletionStateMachine: NodeId
- **DeactivateSleepMode**()
- **DisableAutomaticRun**()
- **EnableAutomaticRun**()
- **FinishJob**()
- **GenerateFileForRead**(GenerateOptions: ProductionDatasetReadOptionsType) → FileNodeId: NodeId, FileHandle: UInt32, CompletionStateMachine: NodeId
- **GenerateFileForWrite**(GenerateOptions: ProductionDatasetWriteOptionsType) → FileNodeId: NodeId, FileHandle: UInt32
- **GetCurrentPage**(VisualisationUnit: UInt32) → Page: Image
- **GetPage**(Id: String) → Page: Image
- **GetPosition**(FileHandle: UInt32) → Position: UInt64
- **GetProductionDatasetInformation**(fileHandle: UInt32) → Information: ProductionDatasetInformationType
- **GetProductionDatasetList**(NameFilter: String, MouldId: String) → ProductionDatasetList: ProductionDatasetInformationType[]
- **InterruptJob**()
- **Load**(Name: String, Components: UInt16[])
- **Open**(Mode: Byte) → FileHandle: UInt32
- **Read**(FileHandle: UInt32, Length: Int32) → Data: ByteString
- **RequestTestSample**()
- **ResetAverageCycleTime**()
- **ResetBoxCounters**()
- **ResetJobCounters**()
- **ResetWatchDog**()
- **Save**(Name: String)
- **SendCyclicJobList**(JobList: CyclicJobListElementType[])
- **SendJobList**(JobList: JobListElementType[])
- **SendProductionDatasetInformation**(fileHandle: UInt32, Information: ProductionDatasetInformationType)
- **SendProductionDatasetList**(ProductionDatasetList: ProductionDatasetInformationType[])
- **SetCyclicJobData**(JobName: String, JobDescription: String, CustomerName: String, ProductionDatasetName: String, ProductionDatasetDescription: String, Material: String[], ProductName: String[], ProductDescription: String[], ContinueAtJobEnd: Boolean, NominalParts: UInt64, NominalBoxParts: UInt64, ExpectedCycleTime: Duration, MouldId: String, NumCavities: UInt32)
- **SetMESMessage**(Id: String, Message: String, Severity: UInt16)
- **SetMachineTime**(DateTime: DateTime, TimeZoneOffset: TimeZoneDataType)
- **SetPosition**(FileHandle: UInt32, Position: UInt64)
- **SetWatchDogTime**(WatchDogTime: Int32)
- **StartJob**()
- **StopAtCycleEnd**()
- **Write**(FileHandle: UInt32, Data: ByteString)

## Jednotky (EngineeringUnits)

Specifikace jednotky nepředepisuje. V NodeSetu je `UnitId = -1` a text 17.3 dává jen nápovědy („RPM“, „e.g. mm/s“), takže je volí server. Návrh odpovídá tomu, co běžně ukazují ovládací panely lisů. Kódy jsou UNECE Rec. 20, `NamespaceUri = http://www.opcfoundation.org/UA/units/un/cefact`.

| Veličina | Uzly | Jednotka | UNECE | UnitId |
| --- | --- | --- | --- | --- |
| Teplota | `ActualTemperature`, `NominalTemperature`, `*DeviationTemperature*`, `StandbyTemperature` | °C | CEL | 4408652 |
| Objem | `CushionVolume`, `PlastificationVolume`, `Decompression*`, `TransferVolume`, `ScrewVolume` | cm³ | CMQ | 4410705 |
| Tlak | `*Pressure*` | bar | BAR | 4342098 |
| Dráha | `CushionStroke`, `TransferStroke`, `InjectionStartPosition`, `VPChangeOverPosition`, `MaxScrewStroke`, `ScrewDiameter` | mm | MMT | 5066068 |
| Rychlost vstřiku a obvodová rychlost | `InjectionSpeed*`, `PlastificationCircumferentialSpeed*` | mm/s | C16 | 4403510 |
| Otáčky | `PlastificationRotationalSpeed*` | 1/min | RPM | 5394509 |
| Bezrozměrné | `FlowIndex` | 1 | C62 | 4404786 |
| Čas | `InjectionTime`, `DosingTime`, `CycleTime`, `LastCycleTime`, `AverageCycleTime`, `ExpectedCycleTime` | ms (Duration) | bez EU | – |

## Výchozí rozsahy pro profily

Typické hodnoty pro lis s uzavírací silou kolem 1000 kN a šnekem 30–40 mm, materiál PP nebo PA6. Nejsou z reálného stroje, jen z běžné praxe. Používají se jako `EURange` a výchozí parametry generátorů.

| Uzel | Arburg-like | Zhafir-like | EURange |
| --- | --- | --- | --- |
| `ScrewDiameter` | 30 mm | 40 mm | – |
| Zóny válce | 5 + tryska | 5 + tryska | 0–450 °C |
| `NominalTemperature` válce | 220–260 °C | 220–260 °C | 0–450 °C |
| Zóny formy (temperace/horký vtok) | 2 + 4 hot runner | 2 | 0–250 °C |
| `PowerUnit_1/ActualTemperature` (olej) | – (elektrický lis, `PowerUnit` jen `PowerOn`) | 40–50 °C | 0–100 °C |
| `CushionVolume` | 3–6 cm³ | 4–8 cm³ | 0–50 cm³ |
| `PlastificationVolume` | 25–45 cm³ | 40–80 cm³ | 0–200 cm³ |
| `SpecificPressureMaximum` | 900–1500 bar | 900–1500 bar | 0–2500 bar |
| `HoldSpecificPressureMaximum` | 500–800 bar | 500–800 bar | 0–2500 bar |
| `BackPressure` | 60–120 bar | 60–120 bar | 0–300 bar |
| `InjectionTime` | 0,6–1,2 s | 0,8–1,5 s | – |
| `DosingTime` | 3–6 s | 4–8 s | – |
| `ExpectedCycleTime` | 18–25 s | 22–30 s | – |
| `DeviceClass` | `Injection Moulding Machine` | `Injection Moulding Machine` | – |

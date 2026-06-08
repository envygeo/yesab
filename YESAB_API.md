# YESAB API
  
The public versioned integration API is under [https://yesabregistry.ca/api/v1/integration/projects](https://yesabregistry.ca/api/v1/integration/projects)

 - *Also see [YESAB project file map](https://yesab.ca/project-map)*
  
With query parameters, such as of startYear and endYear:
[https://yesabregistry.ca/api/v1/integration/projects?startYear=2024&amp;endYear=2025](https://yesabregistry.ca/api/v1/integration/projects?startYear=2024&amp;endYear=2025)

As of June 8, 2026, the v1 endpoint renamed a few project fields compared
with the retired endpoint. Cache refresh normalizes `projectTitle` to `title`,
`projectProponent` to `proponentName`, `projectLocation` to `locations`, and
`projectStageHistory` `start`/`end` values to `stageStart`/`stageEnd` for
existing map consumers.

Get direct records by **path** and **not query** parameters (&amp; and ?):
  
by Project Number
[https://yesabregistry.ca/api/v1/integration/projects/2023-0053](https://yesabregistry.ca/api/v1/integration/projects/2023-0053)
  
by Project ID:
[https://yesabregistry.ca/api/v1/integration/projects/86edab28-da07-47d0-bc82-bf16223f9256](https://yesabregistry.ca/api/v1/integration/projects/86edab28-da07-47d0-bc82-bf16223f9256)

The API will silently return 0 bytes but no error if it doesn't like the size the returned results. So chunk the start and end years by 2 or 3, e.g. startYear=2024&amp;endYear=2026.

There don't appear to be queries like "list all project ID's or Names in the system".

| 0 |  | 
| :--- | :--- |
| projectId | "8525a1fc-cbde-4f09-95b2-ae333929f192" | 
| projectTypeId | "d0ab87c1-4f41-4151-9889-a3ae6f2e45af" | 
| projectTypeName | "Evaluation" | 
| projectNumber | "2025-0100" | 
| title | "Chum Salmon Restoration Trial - Fishing Branch River" | 
| proponentName | "Vuntut Gwitchin First Nation" | 
| assessmentDistricts | \[ {…} ] | 
| sectors | \[ {…} ] | 
| indigenousGovernments | \[ "Vuntut Gwitchin First Nation" ] | 
| decisionBodies | \[] | 
| projectScope | { summary: "", activities: "" } | 
| planningCommissions | \[ "North Yukon Regional Planning Commission" ] | 
| stage | { stageId: "9a1526b4-e220-44a4-80d5-9296a39f7c1c", name: "Adequacy Information Request", extended: false, … } | 
| stageHistory | (3)\[ {…}, {…}, {…} ] | 
| outcomes | {} | 
| stageId | "9a1526b4-e220-44a4-80d5-9296a39f7c1c" | 
| locations |  | 
| 0 | { latitude: 66.51142, longitude: -139.28963 } | 
  
  
*From \< [https://yesabregistry.ca/api/v1/integration/projects?startYear=2025&amp;endYear=2025](https://yesabregistry.ca/api/v1/integration/projects?startYear=2025&amp;endYear=2025)&gt;**  
***  
***  

| ojectId | "86edab28-da07-47d0-bc82-bf16223f9256" | 
| :--- | :--- |
| projectTypeId | "d0ab87c1-4f41-4151-9889-a3ae6f2e45af" | 
| projectTypeName | "Evaluation" | 
| projectNumber | "2023-0053" | 
| title | "Casino-Rude Road Construction Project" | 
| proponentName | "TMM Goldcorp Inc." | 
| assessmentDistricts |  | 
| 0 |  | 
| assessmentDistrictId | "d2ebaae3-6422-4598-92e1-f0b6d61df474" | 
| name | "Mayo" | 
| sectors |  | 
| 0 |  | 
| sectorId | "41ffc2b0-c99e-416c-be0a-96327eb26408" | 
| name | "Transportation - Roads, Access Roads and Trails" | 
| indigenousGovernments |  | 
| 0 | "Selkirk First Nation" | 
| 1 | "Tr'ondëk Hwëch'in" | 
| 2 | "White River First Nation" | 
| decisionBodies |  | 
| 0 | "DFO Referrals Pacific" | 
| 1 | "Transport Canada" | 
| 2 | "YG EMR Land Use Branch" | 
| projectScope |  | 
| summary | "The Project is a proposed road to support the Proponent’s current and future placer mining and exploration operations in that area (currently PM20-041-1). The Project will consist of the on and off-claim construction (up to 2.8 km), upgrading (up to 11.1 km), and use of a new year-round road, including the installation of up to two new culverts. The road will connect the road system at Rude Creek with the barge landing at the mouth of Britannia Creek on the Yukon River.\nRoad construction will begin as soon as permitted and is expected to occur over the course of two seasons. Annual road maintenance will be undertaken as required. Road use will occur until road decommissioning; either when the Casino Tote Road is upgraded to an all-weather road or until the expiry of PM15-083 (2026) and PM20-041-1 (2030). The project area is approximately 100 km west of Pelly Crossing and 86 km northwest of Minto, located within the Selkirk First Nation Traditional Territory and Asserted Territory of the White River First Nation.\nThe Project was originally deemed an amendment to YESAB 2022-0057 however, the Proponent has identified that the Project will not support YESAB 2022-0057, but other placer mining and exploration operations in the same area (ie. PM20-041-1).\nChanges to the Project Scope are highlighted in BoldRevised temporal scope is highlighted in Bold Underline\n" | 
| activities | "Road Construction and Upgrading\n•\tUpgrade existing trails and roads to 4 m in width (11.1 km)\n•\tNew road construction – Up to 2.8 km\n•\tVegetation stripping to occur outside of bird nesting window\n•\tPermafrost regions in riparian areas to be hand brushed and have 4 m wide by 1 m thick layer of fill/tailings as part of road construction\n•\tTemporary road and lane closures\n•\tSignage posted when any equipment working on road\n•\tThe entire length of the road will be constructed/upgraded at one time\nRoad Use and Maintenance – restricted to below 20km/hr\n•\tFor essential use only\n•\tWeekly grader maintenance\n•\tSignage posted when any equipment working on road\nCulvert Installation and Use\n•\tCulvert (1) installation on UNRLT of Dip Creek\n\n\tDiameter 45-91 cm, length 10-13 m\n\n•\tCulvert (1) installation on Casino Creek\n\n\tInstalled in creek bed \n\n•\tAnnual maintenance as required\nBorrow Pits\n•\tUp to 3 Borrow Pits (2 as contingencies) on Casino and Dip Creeks\n•\tUp to 1 500 m3 of fill to be used for Casino Creek crossing unless tailings from mining are available\n•\tDeveloped and reclaimed as needed\nFuel Use and Storage\n•\tNo additional fuel use or storage is proposed for the Project. All fuel use and storage amounts and procedures will fall within the assessed amounts and procedures identified in YESAB 2022-0057\nCamp\n•\tNo camp facilities are proposed for the Project\n•\tWorkers will be billeted in either the camp assessed as part of YESAB 2022-0057 or camp permitted as part of PM20-041-1\n\nReclamation – Road will be reclaimed when Casino Tote Road is upgraded to all-weather road or at the end of 25 years\n\n" | 
| planningCommissions |  | 

*From [https://yesabregistry.ca/api/v1/integration/projects/86edab28-da07-47d0-bc82-bf16223f9256](https://yesabregistry.ca/api/v1/integration/projects/86edab28-da07-47d0-bc82-bf16223f9256)**

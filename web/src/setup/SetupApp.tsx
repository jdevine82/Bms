// The setup area of the web app (milestone 5B). Changes are accepted only on the server
// itself until logins exist (milestone 6).
import { AlarmDefinitionsPage } from "../alarms/AlarmDefinitionsPage";
import { DeliverySettingsPage } from "../alarms/DeliverySettingsPage";
import { AuditPage } from "../auth/AuditPage";
import { SecurityPage } from "../auth/SecurityPage";
import { UsersPage } from "../auth/UsersPage";
import { DeviceTemplatesPage } from "./DeviceTemplatesPage";
import { DiscoverPage } from "./DiscoverPage";
import { EquipTemplatesPage } from "./EquipTemplatesPage";
import { GraphicsSetupPage } from "./GraphicsSetupPage";
import { ItemsPage } from "./ItemsPage";
import { MappingPage } from "./MappingPage";
import type { Section } from "./nav";
import { navigate, useHash } from "./nav";
import { SitePage } from "./SitePage";
import "./setup.css";
import { WizardPage } from "./Wizard";

const SECTIONS: { key: Section; label: string; group: string }[] = [
  { key: "commission", label: "Commission a device", group: "Commissioning" },
  { key: "discover", label: "Discover", group: "Commissioning" },
  { key: "mapping", label: "Mapping table", group: "Commissioning" },
  { key: "devices", label: "Devices", group: "Setup" },
  { key: "buses", label: "Buses and networks", group: "Setup" },
  { key: "rooms", label: "Rooms", group: "Setup" },
  { key: "equipment", label: "Equipment", group: "Setup" },
  { key: "device-templates", label: "Device templates", group: "Templates" },
  { key: "equip-templates", label: "Equipment templates", group: "Templates" },
  { key: "graphics", label: "Graphics", group: "Templates" },
  { key: "alarm-definitions", label: "Alarm definitions", group: "Alarms" },
  { key: "alarm-delivery", label: "Alarm delivery", group: "Alarms" },
  { key: "site", label: "Site and setup file", group: "Site" },
  { key: "users", label: "Users", group: "Site" },
  { key: "security", label: "Security", group: "Site" },
  { key: "audit", label: "Audit log", group: "Site" },
];

export function SetupApp() {
  const hash = useHash();
  const section = (SECTIONS.find((s) => s.key === hash.section)?.key ?? "commission") as Section;
  const groups = [...new Set(SECTIONS.map((s) => s.group))];
  return (
    <div className="setup">
      <nav className="setup-nav" aria-label="Setup">
        {groups.map((g) => (
          <div key={g}>
            <h3>{g}</h3>
            {SECTIONS.filter((s) => s.group === g).map((s) => (
              <a
                key={s.key}
                href={`#/setup/${s.key}`}
                className={section === s.key ? "active" : ""}
                aria-current={section === s.key ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(s.key);
                }}
              >
                {s.label}
              </a>
            ))}
          </div>
        ))}
      </nav>
      <div className="setup-body">
        {section === "commission" && <WizardPage />}
        {section === "discover" && <DiscoverPage />}
        {section === "mapping" && <MappingPage />}
        {section === "devices" && <ItemsPage key="devices" kind="devices" />}
        {section === "buses" && <ItemsPage key="buses" kind="buses" />}
        {section === "rooms" && <ItemsPage key="spaces" kind="spaces" />}
        {section === "equipment" && <ItemsPage key="equips" kind="equips" />}
        {section === "device-templates" && <DeviceTemplatesPage />}
        {section === "equip-templates" && <EquipTemplatesPage />}
        {section === "graphics" && <GraphicsSetupPage />}
        {section === "site" && <SitePage />}
        {section === "alarm-definitions" && <AlarmDefinitionsPage />}
        {section === "alarm-delivery" && <DeliverySettingsPage />}
        {section === "users" && <UsersPage />}
        {section === "security" && <SecurityPage />}
        {section === "audit" && <AuditPage />}
      </div>
    </div>
  );
}

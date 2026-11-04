import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { HmiApp } from "./hmi/HmiApp";
import "./styles.css";

// The Pi kiosk opens /hmi (milestone 3 spec 4); everything else is the site web app.
const isHmi = location.pathname === "/hmi" || location.pathname.startsWith("/hmi/");

createRoot(document.getElementById("root")!).render(<StrictMode>{isHmi ? <HmiApp /> : <App />}</StrictMode>);

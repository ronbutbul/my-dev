import React, { useEffect, useState } from "react";
import Keycloak from "keycloak-js";

const getConfig = () => (window as any).__APP_CONFIG__ || {};

let kcInstance: Keycloak | null = null;

export const KeycloakAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<"loading" | "authenticated" | "disabled">("loading");

  useEffect(() => {
    const cfg = getConfig();
    const kcUrl = cfg.keycloakUrl;
    const kcRealm = cfg.keycloakRealm;
    const kcClientId = cfg.keycloakClientId;

    // If keycloak is not configured, skip auth (local dev)
    if (!kcUrl || !kcRealm || !kcClientId) {
      console.log("Keycloak not configured — skipping auth (local dev mode)");
      setState("disabled");
      return;
    }

    const kc = new Keycloak({ url: kcUrl, realm: kcRealm, clientId: kcClientId });
    kcInstance = kc;

    kc.init({ onLoad: "login-required", checkLoginIframe: false })
      .then((authenticated) => {
        if (authenticated) {
          console.log("Keycloak authenticated");
          setState("authenticated");
        } else {
          kc.login();
        }
      })
      .catch((err) => {
        console.error("Keycloak init failed:", err);
        setState("disabled");
      });
  }, []);

  if (state === "loading") {
    return <div style={{ padding: 24, fontFamily: "sans-serif" }}>Authenticating...</div>;
  }

  return <>{children}</>;
};

export const getKeycloak = () => kcInstance;

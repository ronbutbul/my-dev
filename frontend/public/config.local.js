// Local development config — no keycloak, direct stats API
window.__APP_CONFIG__ = {
  gatewayWsUrl: 'wss://gateway.testmpr.aws2.rafael.co.il',
  statsApiUrl: 'https://statistics.testmpr.aws2.rafael.co.il',
  keycloakUrl: 'https://keycloak.testmpr.aws2.rafael.co.il',
  keycloakRealm: 'stats',
  keycloakClientId: 'frontend',
};

import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { App } from "./App";
import { AdminPage } from "./AdminPage";
import { KeycloakAuth } from "./KeycloakAuth";

const container = document.getElementById("root");

if (container) {
  const root = createRoot(container);
  root.render(
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route
          path="/admin"
          element={
            <KeycloakAuth>
              <AdminPage />
            </KeycloakAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}

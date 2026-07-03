import { setupServer } from "msw/node";

// Empty default handlers; each test installs its own with server.use(...).
export const server = setupServer();

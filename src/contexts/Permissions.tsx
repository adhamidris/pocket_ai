import React from "react";

export type Permissions = {
  canViewPII: boolean;
  canBulkAssign: boolean;
  canBulkResolve: boolean;
  canArchive: boolean;
};

const defaultPermissions: Permissions = {
  canViewPII: false,
  canBulkAssign: true,
  canBulkResolve: true,
  canArchive: false,
};

export const PermissionsContext = React.createContext<Permissions>(defaultPermissions);

export const PermissionsProvider = ({ children, value }: { children: React.ReactNode; value?: Partial<Permissions> }) => {
  const merged = { ...defaultPermissions, ...(value || {}) } as Permissions;
  return <PermissionsContext.Provider value={merged}>{children}</PermissionsContext.Provider>;
};

export const usePermissions = () => React.useContext(PermissionsContext);


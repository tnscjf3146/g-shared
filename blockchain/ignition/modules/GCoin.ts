import { buildModule } from "@nomicfoundation/hardhat-ignition/modules";

export default buildModule("GCoinModule", (m) => {
  const initialHolder = m.getParameter("initialHolder", m.getAccount(0));
  const gCoin = m.contract("GCoin", [initialHolder]);
  return { gCoin };
});

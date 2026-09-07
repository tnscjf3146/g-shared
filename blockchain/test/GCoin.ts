import assert from "node:assert/strict";
import { after, describe, it } from "node:test";

import { network } from "hardhat";
import { getAddress, parseAbi, parseUnits, zeroAddress } from "viem";

import GCoinModule from "../ignition/modules/GCoin.js";

const SUPPLY = parseUnits("1000000", 18);

describe("G Coin", async () => {
  const connection = await network.create();
  const { viem, networkHelpers, ignition } = connection;
  after(async () => connection.close());

  async function deployGCoin() {
    const [deployer, holder, recipient, spender, stranger] =
      await viem.getWalletClients();
    const token = await viem.deployContract("GCoin", [holder.account.address]);
    const publicClient = await viem.getPublicClient();
    return { token, publicClient, deployer, holder, recipient, spender, stranger };
  }

  it("uses G Coin / GSC and 18 decimals", async () => {
    const { token } = await networkHelpers.loadFixture(deployGCoin);
    assert.equal(await token.read.name(), "G Coin");
    assert.equal(await token.read.symbol(), "GSC");
    assert.equal(await token.read.decimals(), 18);
  });

  it("issues exactly one million GSC to the chosen holder, not the deployer", async () => {
    const { token, deployer, holder } = await networkHelpers.loadFixture(deployGCoin);
    assert.equal(await token.read.INITIAL_SUPPLY(), SUPPLY);
    assert.equal(await token.read.totalSupply(), SUPPLY);
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([deployer.account.address]), 0n);
  });

  it("rejects a zero initial holder", async () => {
    await assert.rejects(
      viem.deployContract("GCoin", [zeroAddress]),
      /ERC20InvalidReceiver/,
    );
  });

  it("transfers fractional GSC, emits Transfer, and preserves total supply", async () => {
    const { token, publicClient, holder, recipient } =
      await networkHelpers.loadFixture(deployGCoin);
    const amount = parseUnits("10.25", 18);
    const hash = await token.write.transfer([recipient.account.address, amount], {
      account: holder.account,
    });
    const receipt = await publicClient.waitForTransactionReceipt({ hash });
    assert.equal(receipt.status, "success");
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY - amount);
    assert.equal(await token.read.balanceOf([recipient.account.address]), amount);
    assert.equal(await token.read.totalSupply(), SUPPLY);
    const events = await token.getEvents.Transfer({}, {
      fromBlock: receipt.blockNumber,
      toBlock: receipt.blockNumber,
    });
    assert.equal(events.length, 1);
    assert.deepEqual(events[0].args, {
      from: getAddress(holder.account.address),
      to: getAddress(recipient.account.address),
      value: amount,
    });
  });

  it("approval grants a limit without moving tokens; transferFrom consumes that limit", async () => {
    const { token, holder, recipient, spender } =
      await networkHelpers.loadFixture(deployGCoin);
    const allowance = parseUnits("20", 18);
    const amount = parseUnits("10", 18);
    await token.write.approve([spender.account.address, allowance], {
      account: holder.account,
    });
    assert.equal(await token.read.allowance([holder.account.address, spender.account.address]), allowance);
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([recipient.account.address]), 0n);

    await token.write.transferFrom(
      [holder.account.address, recipient.account.address, amount],
      { account: spender.account },
    );
    assert.equal(await token.read.allowance([holder.account.address, spender.account.address]), allowance - amount);
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY - amount);
    assert.equal(await token.read.balanceOf([recipient.account.address]), amount);
    assert.equal(await token.read.totalSupply(), SUPPLY);
  });

  it("rejects a spender without approval and leaves balances unchanged", async () => {
    const { token, holder, recipient, stranger } =
      await networkHelpers.loadFixture(deployGCoin);
    await assert.rejects(
      token.write.transferFrom(
        [holder.account.address, recipient.account.address, 1n],
        { account: stranger.account },
      ),
      /ERC20InsufficientAllowance/,
    );
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([recipient.account.address]), 0n);
  });

  it("rejects spending beyond the approved limit without consuming allowance", async () => {
    const { token, holder, recipient, spender } =
      await networkHelpers.loadFixture(deployGCoin);
    await token.write.approve([spender.account.address, 10n], { account: holder.account });
    await assert.rejects(
      token.write.transferFrom(
        [holder.account.address, recipient.account.address, 11n],
        { account: spender.account },
      ),
      /ERC20InsufficientAllowance/,
    );
    assert.equal(await token.read.allowance([holder.account.address, spender.account.address]), 10n);
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
  });

  it("rejects direct transfers beyond the balance", async () => {
    const { token, holder, recipient } = await networkHelpers.loadFixture(deployGCoin);
    await assert.rejects(
      token.write.transfer([recipient.account.address, SUPPLY + 1n], { account: holder.account }),
      /ERC20InsufficientBalance/,
    );
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([recipient.account.address]), 0n);
  });

  it("rolls back allowance when transferFrom fails for insufficient balance", async () => {
    const { token, holder, recipient, spender } = await networkHelpers.loadFixture(deployGCoin);
    const amount = SUPPLY + 1n;
    await token.write.approve([spender.account.address, amount], { account: holder.account });
    await assert.rejects(
      token.write.transferFrom(
        [holder.account.address, recipient.account.address, amount],
        { account: spender.account },
      ),
      /ERC20InsufficientBalance/,
    );
    assert.equal(await token.read.allowance([holder.account.address, spender.account.address]), amount);
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([recipient.account.address]), 0n);
  });

  it("rejects transfers to zero and preserves supply and allowance", async () => {
    const { token, holder, spender } = await networkHelpers.loadFixture(deployGCoin);
    await assert.rejects(
      token.write.transfer([zeroAddress, 1n], { account: holder.account }),
      /ERC20InvalidReceiver/,
    );
    await token.write.approve([spender.account.address, 1n], { account: holder.account });
    await assert.rejects(
      token.write.transferFrom([holder.account.address, zeroAddress, 1n], { account: spender.account }),
      /ERC20InvalidReceiver/,
    );
    assert.equal(await token.read.allowance([holder.account.address, spender.account.address]), 1n);
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
    assert.equal(await token.read.totalSupply(), SUPPLY);
  });

  it("offers no external mint even to the deployer or initial holder", async () => {
    const { token, publicClient, deployer, holder, stranger } =
      await networkHelpers.loadFixture(deployGCoin);
    const mintAbi = parseAbi(["function mint(address to, uint256 amount)"]);
    for (const wallet of [deployer, holder, stranger]) {
      await assert.rejects(
        publicClient.simulateContract({
          address: token.address,
          abi: mintAbi,
          functionName: "mint",
          args: [wallet.account.address, 1n],
          account: wallet.account,
        }),
        /revert/i,
      );
    }
    assert.equal(await token.read.totalSupply(), SUPPLY);
    assert.equal(await token.read.balanceOf([holder.account.address]), SUPPLY);
  });

  it("deploys through Ignition with an explicit initial holder", async () => {
    const [, holder] = await viem.getWalletClients();
    const { gCoin } = await ignition.deploy(GCoinModule, {
      parameters: { GCoinModule: { initialHolder: holder.account.address } },
    });
    assert.equal(await gCoin.read.balanceOf([holder.account.address]), SUPPLY);
    assert.equal(await gCoin.read.totalSupply(), SUPPLY);
  });
});

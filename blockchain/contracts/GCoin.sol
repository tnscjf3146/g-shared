// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice Fixed-supply payment token for GShared GPU jobs.
contract GCoin is ERC20 {
    /// @dev Base units: 1,000,000 GSC with 18 decimals.
    uint256 public constant INITIAL_SUPPLY = 1_000_000 * 10 ** 18;

    /// @param initialHolder Receives the full supply; ERC20 rejects address(0).
    constructor(address initialHolder) ERC20("G Coin", "GSC") {
        _mint(initialHolder, INITIAL_SUPPLY);
    }
}

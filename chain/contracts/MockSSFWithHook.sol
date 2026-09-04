// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface ITokenReceiver {
    function onTokenReceived(address from, uint256 amount) external;
}

/// 수신자가 컨트랙트면 콜백을 부르는 토큰.
/// 실제 ERC-777 이나 일부 토큰이 이렇게 동작하며, 재진입 공격의 통로가 된다.
contract MockSSFWithHook {
    string public name = "Hooked SSAFY";
    string public symbol = "hSSF";
    uint8  public constant decimals = 0;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);

    constructor(uint256 initialSupply_) {
        totalSupply = initialSupply_;
        balanceOf[msg.sender] = initialSupply_;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "insufficient allowance");
        allowance[from][msg.sender] = allowed - amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) private {
        require(balanceOf[from] >= amount, "exceeds balance");
        balanceOf[from] -= amount;
        balanceOf[to]   += amount;
        emit Transfer(from, to, amount);

        // 수신자가 컨트랙트면 콜백을 부른다. 여기가 공격 통로다.
        if (to.code.length > 0) {
            try ITokenReceiver(to).onTokenReceived(from, amount) {} catch {}
        }
    }
}

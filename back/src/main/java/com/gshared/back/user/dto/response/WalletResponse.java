package com.gshared.back.user.dto.response;

public record WalletResponse(String walletAddress) {

    public static WalletResponse of(String walletAddress) {
        return new WalletResponse(walletAddress);
    }
}

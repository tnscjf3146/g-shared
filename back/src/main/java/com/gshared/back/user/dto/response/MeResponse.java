package com.gshared.back.user.dto.response;

import com.gshared.back.user.domain.User;

/** 내 계정. 지갑은 /api/users/me/wallet 이 따로 답한다. */
public record MeResponse(
        String email,
        String nickname
) {
    public static MeResponse from(User user) {
        return new MeResponse(user.getEmail(), user.getNickname());
    }
}

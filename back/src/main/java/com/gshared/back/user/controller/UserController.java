package com.gshared.back.user.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.user.dto.request.UpdateNicknameRequest;
import com.gshared.back.user.dto.response.MeResponse;
import com.gshared.back.user.service.UserService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 내 계정 조회·수정. {@link UserWalletController} 와 같은 패턴 — SecurityConfig 의 anyRequest().authenticated() 에
 * 걸리므로 별도 허용 규칙 없이 로그인이 강제되고, 대상 사용자는 인증 정보에서 가져온다.
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/users/me")
public class UserController {

    private final UserService userService;

    @GetMapping
    public ResponseEntity<MeResponse> getMe(@AuthenticationPrincipal CustomUserDetails userDetails) {
        return ResponseEntity.ok(userService.getMe(userDetails.getId()));
    }

    @PatchMapping
    public ResponseEntity<Void> updateNickname(
            @AuthenticationPrincipal CustomUserDetails userDetails,
            @Valid @RequestBody UpdateNicknameRequest request
    ) {
        userService.updateNickname(userDetails.getId(), request.nickname());
        return ResponseEntity.noContent().build();
    }
}

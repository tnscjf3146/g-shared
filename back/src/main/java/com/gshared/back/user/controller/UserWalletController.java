package com.gshared.back.user.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.user.dto.request.WalletChallengeRequest;
import com.gshared.back.user.dto.request.WalletLinkRequest;
import com.gshared.back.user.dto.response.WalletChallengeResponse;
import com.gshared.back.user.dto.response.WalletResponse;
import com.gshared.back.user.service.UserWalletService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

/**
 * SecurityConfig 의 anyRequest().authenticated() 에 걸리므로 별도 허용 규칙 없이 로그인이 강제된다.
 * 대상 사용자는 요청 본문이 아니라 인증 정보에서 가져온다.
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/users/me/wallet")
public class UserWalletController {

    private final UserWalletService userWalletService;

    @PostMapping("/challenge")
    public ResponseEntity<WalletChallengeResponse> issueChallenge(
            @AuthenticationPrincipal CustomUserDetails userDetails,
            @Valid @RequestBody WalletChallengeRequest request
    ) {
        return ResponseEntity.ok(userWalletService.issueChallenge(userDetails.getId(), request));
    }

    @PostMapping
    public ResponseEntity<WalletResponse> link(
            @AuthenticationPrincipal CustomUserDetails userDetails,
            @Valid @RequestBody WalletLinkRequest request
    ) {
        return ResponseEntity.ok(userWalletService.link(userDetails.getId(), request));
    }

    @DeleteMapping
    public ResponseEntity<Void> unlink(@AuthenticationPrincipal CustomUserDetails userDetails) {
        userWalletService.unlink(userDetails.getId());
        return ResponseEntity.noContent().build();
    }
}

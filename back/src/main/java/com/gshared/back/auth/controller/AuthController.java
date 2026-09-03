package com.gshared.back.auth.controller;

import com.gshared.back.auth.dto.request.SignupRequest;
import com.gshared.back.auth.dto.response.TokenResponse;
import com.gshared.back.auth.service.AuthService;
import com.gshared.back.global.security.userdetails.CustomUserDetails;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/auth")
public class AuthController {

    private final AuthService authService;

    @PostMapping("/signup")
    public ResponseEntity<Void> signup(@Valid @RequestBody SignupRequest request) {
        authService.signup(request);
        return ResponseEntity.status(HttpStatus.CREATED).build();
    }

    @PostMapping("/reissue")
    public ResponseEntity<TokenResponse> reissue(
            @CookieValue(name = "refreshToken", required = false) String refreshToken,
            HttpServletResponse response
    ) {
        return ResponseEntity.ok(TokenResponse.of(authService.reissue(refreshToken, response)));
    }

    @PostMapping("/logout")
    public ResponseEntity<Void> logout(HttpServletRequest request, HttpServletResponse response, @AuthenticationPrincipal CustomUserDetails userDetails) {
        authService.logout(request, response, userDetails.getId());
        return ResponseEntity.noContent().build();
    }
}

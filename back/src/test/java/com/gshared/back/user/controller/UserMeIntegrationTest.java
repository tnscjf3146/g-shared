package com.gshared.back.user.controller;

import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 내 계정 조회·닉네임 변경 (2026-09-15 프론트 API 요청 ①). 보안 필터를 거쳐 확인한다.
 * 닉네임 규칙은 회원가입과 같아야 한다 — 가입 때 되던 이름이 변경 때 거절되면 안 된다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class UserMeIntegrationTest {

    private static final String ME_URL = "/api/users/me";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private JwtProvider jwtProvider;

    private User user;
    private String token;

    @BeforeEach
    void setUp() {
        user = userRepository.save(User.createUser(
                "me-" + UUID.randomUUID() + "@gshared.io", "encoded", "가을"));
        token = jwtProvider.createAccessToken(user.getId(), user.getRole());
    }

    private ResultActions patchNickname(String body) throws Exception {
        return mockMvc.perform(patch(ME_URL)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body));
    }

    @Test
    @DisplayName("GET /api/users/me — 이메일·닉네임을 준다")
    void getMe() throws Exception {
        mockMvc.perform(get(ME_URL).header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value(user.getEmail()))
                .andExpect(jsonPath("$.nickname").value("가을"));
    }

    @Test
    @DisplayName("PATCH /api/users/me — 닉네임이 바뀌고 204")
    void updateNickname() throws Exception {
        patchNickname("{\"nickname\":\"새닉네임1\"}").andExpect(status().isNoContent());

        assertThat(userRepository.findById(user.getId()).orElseThrow().getNickname()).isEqualTo("새닉네임1");
        mockMvc.perform(get(ME_URL).header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(jsonPath("$.nickname").value("새닉네임1"));
    }

    @Test
    @DisplayName("닉네임 규칙 위반(길이·문자·공백)은 400 INVALID_INPUT — 회원가입과 같은 규칙")
    void invalidNickname() throws Exception {
        patchNickname("{\"nickname\":\"a\"}")                     // 2자 미만
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_INPUT"));
        patchNickname("{\"nickname\":\"" + "가".repeat(16) + "\"}")   // 15자 초과
                .andExpect(status().isBadRequest());
        patchNickname("{\"nickname\":\"nick name\"}")             // 공백
                .andExpect(status().isBadRequest());
        patchNickname("{\"nickname\":\"nick!\"}")                 // 특수문자
                .andExpect(status().isBadRequest());
        patchNickname("{}")                                       // 누락
                .andExpect(status().isBadRequest());

        assertThat(userRepository.findById(user.getId()).orElseThrow().getNickname()).isEqualTo("가을");
    }

    @Test
    @DisplayName("토큰 없으면 401")
    void requiresLogin() throws Exception {
        mockMvc.perform(get(ME_URL)).andExpect(status().isUnauthorized());
        mockMvc.perform(patch(ME_URL).contentType(MediaType.APPLICATION_JSON).content("{\"nickname\":\"가을\"}"))
                .andExpect(status().isUnauthorized());
    }
}

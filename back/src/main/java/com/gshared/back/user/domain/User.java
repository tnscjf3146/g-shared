package com.gshared.back.user.domain;

import com.gshared.back.global.event.BaseTimeEntity;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Getter
@Table(name = "users")
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class User extends BaseTimeEntity {

    @Id
    @Column(name = "user_id")
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String email;

    @Column(nullable = false)
    private String password;

    @Column(nullable = false, length = 15)
    private String nickname;

    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private Role role;

    private String walletAddress;

    private User(String email, String password, String nickname) {
        this.email = email;
        this.password = password;
        this.nickname = nickname;
        this.role = Role.USER;
    }

    public static User createUser(String email, String password, String nickname) {
        return new User(email, password, nickname);
    }
}

"""Training-only SAC policy for CrowdNav's zero-exploration action mapping."""

import torch
from stable_baselines3.common.policies import ContinuousCritic
from stable_baselines3.sac.policies import SACPolicy


def effective_action(actions):
    # Nonpositive values all request zero route exploration. Keep the
    # continuous rerouting coordinate so its actor gradient is preserved.
    return torch.cat((actions[:, :1].clamp_min(0), actions[:, 1:]), dim=1)


class CrowdNavCritic(ContinuousCritic):
    def forward(self, obs, actions):
        return super().forward(obs, effective_action(actions))

    def q1_forward(self, obs, actions):
        return super().q1_forward(obs, effective_action(actions))


class CrowdNavSACPolicy(SACPolicy):
    def make_critic(self, features_extractor=None):
        # SB3 uses this factory for both the online and target critic.
        kwargs = self._update_features_extractor(self.critic_kwargs, features_extractor)
        return CrowdNavCritic(**kwargs).to(self.device)

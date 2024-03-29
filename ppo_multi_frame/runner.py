from dataclasses import dataclass
import torch
import numpy as np
from baselines.common.vec_env.shmem_vec_env import ShmemVecEnv
from ppo_multi_frame.model import ActorCritic


@dataclass
class EnvRunner:
    envs: ShmemVecEnv
    model: ActorCritic
    rollout_size: int
    device: str
    emb_stack: int

    ep_reward = []
    ep_len = []

    def get_logs(self):
        if len(self.ep_reward) >= self.envs.num_envs:
            res = {
                '_episode/reward': np.mean(self.ep_reward),
                '_episode/len': np.mean(self.ep_len),
            }
            self.ep_reward.clear(), self.ep_len.clear()
            return res
        else:
            return {}

    def __iter__(self):
        r, n = self.rollout_size, self.envs.num_envs

        def tensor(shape=(r, n, 1), dtype=torch.float):
            return torch.empty(*shape, dtype=dtype, device=self.device)

        obs_shape = self.envs.observation_space.shape
        obs_dtype = torch.uint8 if len(obs_shape) == 3 else torch.float
        obs = tensor((r + 1, n, *obs_shape), dtype=obs_dtype)

        # tremendously memory inefficient, only for POC purposes
        obs_emb = torch.zeros(
            r + 1, n, self.emb_stack, 1, 84, 84,
            device=self.device, dtype=torch.uint8)

        rewards = tensor()
        vals = tensor()
        log_probs = tensor()
        actions = tensor(dtype=torch.long)
        masks = tensor()

        step = 0
        obs[0] = self.envs.reset()
        obs_emb[0, :, -1] = obs[0, :, -1:]

        while True:
            with torch.no_grad():
                dist, vals[step] = self.model(obs[step], obs_emb[step])
                a = dist.sample()
                actions[step] = a.unsqueeze(-1)
                log_probs[step] = dist.log_prob(a).unsqueeze(-1)

            obs[step + 1], rewards[step], terms, infos =\
                self.envs.step(actions[step])
            masks[step] = ~terms

            obs_emb[step + 1, :, :-1].copy_(obs_emb[step, :, 1:])
            obs_emb[step + 1] *= masks[step, ...,
                                       None, None, None].to(dtype=torch.uint8)
            obs_emb[step + 1, :, -1] = obs[step + 1, :, -1:]

            for i, info in enumerate(infos):
                if 'episode' in info.keys():
                    self.ep_reward.append(info['episode']['r'])
                    self.ep_len.append(info['episode']['l'])

            step = (step + 1) % self.rollout_size
            if step == 0:
                yield {'obs': obs,
                       'obs_emb': obs_emb,
                       'rewards': rewards,
                       'vals': vals,
                       'log_probs': log_probs,
                       'actions': actions,
                       'masks': masks,
                       }
                obs[0].copy_(obs[-1])
                obs_emb[0].copy_(obs_emb[-1])

# wrappers to gym env

import gym
import numpy as np

class AutoReset(gym.Wrapper):
    """Automatically reset the env when it is done"""
    def __init__(self, env):
        super(AutoReset, self).__init__(env)
        
    def step(self, action):
        obs, rew, done, info = self.env.step(action)
        if done:
            obs = self.env.reset()
        return obs, rew, done, info
    
class StartWithRandomActions(gym.Wrapper):
    """ Makes random number of random actions at the beginning of each
    episode. """

    def __init__(self, env, max_random_actions=30):
        super(StartWithRandomActions, self).__init__(env)
        self.max_random_actions = max_random_actions
        self.real_done = True

    def step(self, action):
        obs, rew, done, info = self.env.step(action)
        self.real_done = info.get("real_done", True)
        return obs, rew, done, info

    def reset(self, **kwargs):
        obs = self.env.reset()
        if self.real_done:
            num_random_actions = np.random.randint(
                self.max_random_actions + 1)
            for _ in range(num_random_actions):
                obs, _, _, _ = self.env.step(
                    self.env.action_space.sample())
            self.real_done = False
        return obs
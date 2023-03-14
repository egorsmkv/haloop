
import torch

def ctc_forward_score1(
    emissions, # (T, C)
    targets, # (S,), such that T > S
):
    """
    CTC forward score for a single sequence.

    [Graves06] Connectionist Temporal Classification:
               Labelling Unsegmented Sequence Data with Recurrent Neural Networks
    """
    blank = 0
    T, C = emissions.shape

    # A B C -> _ A _ B _ C _
    _t_a_r_g_e_t_s_ = torch.stack([torch.full_like(targets, blank), targets], dim=0).mT.reshape(-1)
    _t_a_r_g_e_t_s_ = torch.cat([_t_a_r_g_e_t_s_, targets.new_full((1,), blank)], dim=-1)

    log_alpha = emissions.new_full((T, len(_t_a_r_g_e_t_s_)), float('-inf'))

    log_alpha[0, :2] = emissions[0, _t_a_r_g_e_t_s_[:2]]

    for t in range(1, T):
        for s in range(1, len(_t_a_r_g_e_t_s_)):
            self_loop = log_alpha[t-1, s]
            prev_symbol = log_alpha[t-1, s-1]
            skip = log_alpha[t-1, s-2]

            base_transitions = self_loop.logaddexp(prev_symbol)
            transitions_with_skip = base_transitions.logaddexp(skip)

            # transition into blank: no skips across blanks
            transitions = torch.where(
                _t_a_r_g_e_t_s_[s] == blank,
                base_transitions,
                transitions_with_skip
            )

            # transition from the same symbol: must go through a blank (no skips)
            transitions = torch.where(
                _t_a_r_g_e_t_s_[s-2] == _t_a_r_g_e_t_s_[s],
                base_transitions,
                transitions
            )

            log_alpha[t, s] = transitions + emissions[t, _t_a_r_g_e_t_s_[s]]

    return -log_alpha[T-1, -1].logaddexp(log_alpha[T-1, -2])



def ctc_forward_score2(
    emissions, # (T, C)
    targets, # (S,), such that T > S
):
    """
    CTC forward score.

    [Graves06] Connectionist Temporal Classification:
               Labelling Unsegmented Sequence Data with Recurrent Neural Networks
    """

    blank = 0
    T, C = emissions.shape

    # A B C -> _ A _ B _ C _
    _t_a_r_g_e_t_s_ = torch.stack([torch.full_like(targets, blank), targets], dim=0).mT.reshape(-1)
    _t_a_r_g_e_t_s_ = torch.cat([_t_a_r_g_e_t_s_, targets.new_full((1,), blank)], dim=-1)

    log_alpha = emissions.new_full((T, len(_t_a_r_g_e_t_s_)), float('-inf'))

    log_alpha[0, :2] = emissions[0, _t_a_r_g_e_t_s_[:2]]

    # first symbol at t=1 comes from a self loop or a leading blank
    log_alpha[1,  1:2] = log_alpha[0, 0].logaddexp(log_alpha[0, 1]) + emissions[1, _t_a_r_g_e_t_s_[1:2]]

    for t in range(1, T):
        self_loop = log_alpha[t-1, 2:]
        prev_symbol = log_alpha[t-1, 1:-1]
        skip = log_alpha[t-1, :-2]

        # transition into blank: no skips across blanks
        blanks = _t_a_r_g_e_t_s_[2:] == blank
        transitions = torch.where(
            blanks,
            self_loop.logaddexp(prev_symbol),
            self_loop.logaddexp(prev_symbol).logaddexp(skip)
        )

        # transition from the same symbol: must go through a blank (no skips)
        same_symbols = _t_a_r_g_e_t_s_[2:] == _t_a_r_g_e_t_s_[:-2]
        transitions = torch.where(
            same_symbols,
            self_loop.logaddexp(prev_symbol),
            transitions
        )

        if t > 1:
            # first symbol past t=1 only comes from a self loop
            self_loop_ = log_alpha[t-1, 1:]
            log_alpha[t, 1:] = self_loop_ + emissions[t, _t_a_r_g_e_t_s_[1:]]

        log_alpha[t, 2:] = transitions + emissions[t, _t_a_r_g_e_t_s_[2:]]

    return -log_alpha[T-1, -1].logaddexp(log_alpha[T-1, -2])


def ctc_forward_score3(
    emissions, # (T, N, C)
    targets, # (N, S,), such that T > S
    emission_lengths, # (N,)
    target_lengths, # (N,)
):
    """
    CTC forward score for a batch of sequences.

    [Graves06] Connectionist Temporal Classification:
               Labelling Unsegmented Sequence Data with Recurrent Neural Networks
    """

    blank = 0
    T, N, C = emissions.shape

    # A B C -> _ A _ B _ C _
    _t_a_r_g_e_t_s_ = torch.stack([torch.full_like(targets, blank), targets], dim=1).mT.reshape(N, -1)
    _t_a_r_g_e_t_s_ = torch.cat([_t_a_r_g_e_t_s_, targets.new_full((N, 1), blank)], dim=-1)
    S_ = _t_a_r_g_e_t_s_.shape[1] # S_ = 2*S + 1

    T_last = emission_lengths - 1
    S_last = 2*target_lengths + 1 - 1

    log_alpha = emissions.new_full((T, N, S_), float('-inf'))

    log_alpha[0, :, :2] = emissions[0, :].gather(-1, _t_a_r_g_e_t_s_[:, :2])

    # first symbol at t=1 comes from a self loop or a leading blank
    first_transitions = log_alpha[0, :, 0].logaddexp(log_alpha[0, :, 1])
    log_alpha[1, :, 1] = first_transitions + emissions[1, :].gather(-1, _t_a_r_g_e_t_s_[:, 1:2]).squeeze()

    blanks = _t_a_r_g_e_t_s_[:, 2:] == blank
    same_symbols = _t_a_r_g_e_t_s_[:, 2:] == _t_a_r_g_e_t_s_[:, :-2]

    for t in range(1, T):
        self_loop = log_alpha[t-1, :, 2:]
        prev_symbol = log_alpha[t-1, :, 1:-1]
        skip = log_alpha[t-1, :, :-2]

        basic_transitions = self_loop.logaddexp(prev_symbol)

        # transition into blank: no skips across blanks
        transitions = torch.where(
            blanks,
            basic_transitions,
            basic_transitions.logaddexp(skip)
        )

        # transition from the same symbol: must go through a blank (no skips)
        transitions = torch.where(
            same_symbols,
            basic_transitions,
            transitions
        )

        if t > 1:
            # first symbol past t=1 only comes from a self loop
            self_loop_ = log_alpha[t-1, :, 1:]
            log_alpha[t, :, 1:] = self_loop_ + emissions[t].gather(-1, _t_a_r_g_e_t_s_[:, 1:])

        log_alpha[t, :, 2:] = transitions + emissions[t].gather(-1, _t_a_r_g_e_t_s_[:, 2:])

    last_timestep = log_alpha[T_last, torch.arange(N), :]
    last_blank  = last_timestep[torch.arange(N), S_last]
    last_symbol = last_timestep[torch.arange(N), S_last-1]

    return -last_blank.logaddexp(last_symbol)


if __name__ == '__main__':
    torch.manual_seed(2)
    logits0 = torch.randn(5, 7).log_softmax(-1)
    logits = logits0
    print('logits')
    print(logits.T)
    targets = torch.LongTensor([1,2,3])
    print('scores')
    print(ctc_forward_score1(logits, targets))

    print(ctc_forward_score2(logits, targets))

    logits1 = torch.randn(5, 7).log_softmax(-1)
    targets = torch.LongTensor([1,2,3,3])
    targets1 = torch.LongTensor([1,2,3,4])
    input_lengths = torch.LongTensor([5, 5])
    target_lengths = torch.LongTensor([3, 4])
    logits = torch.stack([logits, logits1], dim=1)
    targets = torch.stack([targets, targets1], dim=0)

    print(logits, targets)

    print('torch ctc', torch.nn.functional.ctc_loss(
        logits,
        targets,
        input_lengths,
        target_lengths, blank=0, reduction='none'
    ))

    print('ctc3     ', ctc_forward_score3(
        logits, targets,
        input_lengths,
        target_lengths))

    print('ctc2[0]    ', ctc_forward_score2(logits0, torch.LongTensor([1,2,3])))
    print('ctc2[1]    ', ctc_forward_score2(logits1, targets1))